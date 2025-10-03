from flask import Flask, render_template, jsonify
import requests
import time
import threading
import datetime
import calendar
from dateutil.parser import isoparse
import pytz

app = Flask(__name__)

# --- CONFIGURAÇÕES ---
AGENDOR_API_TOKEN = '8098628e-9312-445d-8534-eed86db7a36e'
SALES_GOALS = {
    'Michelly': 82000,
    'Miguel': 82000,
    'Alisson': 50000,
    'Jaqueline': 50000,
    'Larissa': 50000
}
VALID_SALESPEOPLE = list(SALES_GOALS.keys())
# --- FIM DAS CONFIGURAÇÕES ---

# --- Variáveis Globais ---
last_deal_id = None
latest_deal_info = {}
metrics_data = {}
metrics_lock = threading.Lock()
API_URL = 'https://api.agendor.com.br/v3'
HEADERS = {'Authorization': f'Token {AGENDOR_API_TOKEN}', 'Content-Type': 'application/json'}
SAO_PAULO_TZ = pytz.timezone('America/Sao_Paulo')

def fetch_agendor_data():
    """Busca o negócio ganho mais recente e força a atualização das métricas."""
    global last_deal_id, latest_deal_info
    while True:
        try:
            params = {'dealStatus': '2', 'order_by': '-updatedAt', 'limit': 1}
            response = requests.get(f'{API_URL}/deals', headers=HEADERS, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data['data']:
                newest_deal = data['data'][0]
                new_deal_id = newest_deal['id']
                if new_deal_id != last_deal_id:
                    print(f"\n[INFO] Novo Ganho detectado! ID: {new_deal_id}. Forçando atualização de métricas...")
                    latest_deal_info = {
                        'vendedor': newest_deal['owner']['name'], 'valor': newest_deal['value'],
                        'titulo': newest_deal['title'], 'timestamp': time.time()
                    }
                    last_deal_id = new_deal_id
                    threading.Thread(target=calculate_and_update_metrics).start()
        except requests.exceptions.RequestException as e:
            print(f"[ERRO] Falha na thread de notificação: {e}")
        time.sleep(10)

def fetch_all_items_paginated(endpoint, params):
    """Busca todos os itens de um endpoint, lidando com a paginação."""
    all_items = []
    next_url = f'{API_URL}/{endpoint}'
    first_request = True
    while next_url:
        try:
            current_params = params if first_request else None
            response = requests.get(next_url, headers=HEADERS, params=current_params, timeout=30)
            first_request = False
            response.raise_for_status()
            data = response.json()
            all_items.extend(data['data'])
            next_url = data['links'].get('next')
        except requests.exceptions.RequestException as e:
            print(f"[ERRO] Falha durante a paginação para {endpoint}: {e}")
            break
    return all_items

def calculate_and_update_metrics():
    """Calcula todas as métricas com a nova lógica de conversão."""
    global metrics_data
    with metrics_lock:
        try:
            now_local = datetime.datetime.now(SAO_PAULO_TZ)
            print(f"\n[INFO] Atualizando métricas para {now_local.year}/{now_local.month} (Fuso SP)...")

            # 1. Pega os negócios GANHOS no mês para calcular faturamento e meta
            all_won_deals_ever = fetch_all_items_paginated('deals', {'dealStatus': 2})
            won_deals_this_month_unfiltered = [d for d in all_won_deals_ever if d.get('wonAt') and isoparse(d['wonAt']).astimezone(SAO_PAULO_TZ).month == now_local.month and isoparse(d['wonAt']).astimezone(SAO_PAULO_TZ).year == now_local.year]
            won_deals_this_month = [deal for deal in won_deals_this_month_unfiltered if deal.get('owner', {}).get('name') in VALID_SALESPEOPLE]
            
            # 2. Pega os negócios CRIADOS no mês (nossa "base" para a conversão)
            start_date_str = now_local.replace(day=1).isoformat()
            last_day = calendar.monthrange(now_local.year, now_local.month)[1]
            end_date_str = now_local.replace(day=last_day).isoformat()
            lead_deals_params = {'createdAtGt': start_date_str, 'createdAtLt': end_date_str}
            all_leads_this_month = fetch_all_items_paginated('deals', lead_deals_params)

            salespeople_performance = []
            for name, goal in SALES_GOALS.items():
                # Faturamento e Meta continuam usando os ganhos do mês
                my_won_deals_for_revenue = [d for d in won_deals_this_month if d.get('owner', {}).get('name') == name]
                my_total_value = sum(d.get('value', 0) for d in my_won_deals_for_revenue)
                my_goal_percentage = (my_total_value / goal) * 100 if goal > 0 else 0

                # <<<< AQUI ESTÁ A MUDANÇA NA LÓGICA DE CONVERSÃO >>>>
                # 1. Pega os leads que o vendedor CRIOU este mês
                my_leads_this_month = [l for l in all_leads_this_month if l.get('owner', {}).get('name') == name]
                my_leads_count = len(my_leads_this_month)
                
                # 2. DESSES leads, conta quantos já foram ganhos
                my_converted_deals_from_leads = [l for l in my_leads_this_month if l.get('dealStatus', {}).get('id') == 2]
                my_converted_count = len(my_converted_deals_from_leads)

                # 3. Calcula a conversão com a nova base
                my_conversion_rate = (my_converted_count / my_leads_count) * 100 if my_leads_count > 0 else 0
                # <<<< FIM DA MUDANÇA >>>>

                salespeople_performance.append({
                    "name": name,
                    "goal_percentage": my_goal_percentage,
                    "conversion_rate": my_conversion_rate,
                })

            # Métricas gerais continuam como antes
            total_won_deals = len(won_deals_this_month)
            total_value = sum(deal.get('value', 0) for deal in won_deals_this_month if deal.get('value'))
            average_ticket = total_value / total_won_deals if total_won_deals > 0 else 0
            
            metrics_data = {
                "total_won_deals": total_won_deals, "total_value": total_value,
                "average_ticket": average_ticket,
                "salespeople_performance": sorted(salespeople_performance, key=lambda x: x['goal_percentage'], reverse=True)
            }
            print(f"[INFO] Métricas atualizadas. Total de ganhos válidos: {total_won_deals}, Faturamento: R$ {total_value:,.2f}")

        except Exception as e:
            print(f"[ERRO] Falha crítica no cálculo de métricas: {e}")


def metrics_update_scheduler():
    while True:
        time.sleep(60)
        calculate_and_update_metrics()

@app.route('/')
def home():
    return render_template('painel.html')

# O resto do código continua igual...

@app.route('/check_deal')
def check_deal():
    global latest_deal_info
    if latest_deal_info and (time.time() - latest_deal_info.get('timestamp', 0)) < 15:
        info_to_send = latest_deal_info.copy()
        latest_deal_info = {}
        return jsonify(info_to_send)
    return jsonify({})

@app.route('/get_metrics')
def get_metrics():
    return jsonify(metrics_data)

print(">>> Realizando a primeira carga de métricas...")
calculate_and_update_metrics()

print(">>> Carga inicial completa. Iniciando threads de fundo.")
threading.Thread(target=fetch_agendor_data, daemon=True).start()
threading.Thread(target=metrics_update_scheduler, daemon=True).start()
print(">>> Threads de fundo iniciadas.")

if __name__ == '__main__':
    print(f">>> SERVIDOR DE TESTE PRONTO. Acessível em http://localhost:2112")
    app.run(host='0.0.0.0', port=2112, debug=False)