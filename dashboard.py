import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, db
from io import BytesIO
from datetime import datetime, date
import altair as alt
import xlsxwriter
import re
import time
import os
import json 

# ==============================================================================
# 1. KONFIGURASI & LOGIN
# ==============================================================================

st.set_page_config(layout="wide", page_title="Dashboard X-POS")

# URL Database Firebase
FIREBASE_DB_URL = 'https://xpos.asia-southeast1.firebasedatabase.app'

# Data User Login (Hardcoded)
VALID_USERS = {
    "Admin": "123",
    "Jason": "0000",
    "Rina": "0102",
    "Hendri": "1234",
    "Sandy": "0908"
}

# Init Session
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'user_name' not in st.session_state:
    st.session_state['user_name'] = ""

def login():
    st.markdown("<style>.stTextInput > div > div > input {text-align: center;}</style>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.title("🔒 Login Dashboard")
        st.write("Silakan pilih user dan masukkan PIN.")
        user_select = st.selectbox("Pilih User", list(VALID_USERS.keys()))
        pin_input = st.text_input("PIN", type="password", placeholder="****")
        if st.button("MASUK", use_container_width=True): 
            if pin_input == VALID_USERS.get(user_select):
                st.session_state['logged_in'] = True
                st.session_state['user_name'] = user_select
                st.success(f"Selamat Datang, {user_select}!")
                time.sleep(0.5)
                st.rerun()
            else:
                st.error("PIN Salah!")

def logout():
    st.session_state['logged_in'] = False
    st.session_state['user_name'] = ""
    st.rerun()

# ==============================================================================
# 2. FIREBASE & DATA FETCHING
# ==============================================================================

@st.cache_resource
def initialize_firebase():
    try:
        if not firebase_admin._apps:
            # 1. Prioritas: Cek Streamlit Secrets (Untuk Cloud Hosting)
            if 'firebase_credentials' in st.secrets:
                cred_info = dict(st.secrets['firebase_credentials'])
                cred = credentials.Certificate(cred_info)
                firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
            
            # 2. Fallback: Cek File Lokal (Untuk Localhost)
            else:
                cred_file = None
                if os.path.exists('firebase-credentials.json'):
                    cred_file = 'firebase-credentials.json'
                elif os.path.exists('firebase-credentials.json.json'):
                    cred_file = 'firebase-credentials.json.json'
                elif os.path.exists('serviceAccountKey.json'):
                    cred_file = 'serviceAccountKey.json'
                
                if cred_file:
                    cred = credentials.Certificate(cred_file)
                    firebase_admin.initialize_app(cred, {'databaseURL': FIREBASE_DB_URL})
                else:
                    st.error("⚠️ FILE KUNCI TIDAK DITEMUKAN! (Cek secrets atau file json)")
                    st.stop()
    except Exception as e:
        st.error(f"Firebase Error: {e}"); st.stop()

def fetch_data(branch_name, debug_mode=False):
    # Tidak pakai cache saat debug
    if debug_mode:
        st.cache_data.clear()
        
    try:
        # Ambil data history
        ref = db.reference(f'/{branch_name}/history') 
        data = ref.get()
        
        if debug_mode:
            st.info(f"🔍 Debug Path: /{branch_name}/history")
            if data:
                st.write("Data Tanggal Ditemukan:", list(data.keys()))
            else:
                st.warning("Data History Kosong / None")

        all_transactions = []
        if data and isinstance(data, dict):
            for date_key, content in data.items():
                if isinstance(content, dict):
                    # 1. Cek apakah ada detail TRANSAKSI
                    trx_data = content.get('transactions')
                    
                    if trx_data:
                        if isinstance(trx_data, list):
                            all_transactions.extend([t for t in trx_data if t])
                        elif isinstance(trx_data, dict):
                            all_transactions.extend(list(trx_data.values()))
                    
                    # 2. Jika transaksi kosong, buat DATA DUMMY dari sales_report
                    elif 'sales_report' in content:
                        report = content['sales_report']
                        dummy_trx = {
                            "order_id": f"REPORT-{date_key}",
                            "unique_code": "DAILY-CLOSE",
                            "timestamp": f"{date_key} 23:59:59", 
                            "total_final": report.get('total_sales', 0),
                            "items": [], # Item kosong
                            "status": "completed",
                            "order_type": "Laporan Harian",
                            "payment_method": "-",
                            "discount_amount": 0,
                            "service_charge": 0,
                            "tax_pb1": 0
                        }
                        all_transactions.append(dummy_trx)
        
        return all_transactions
    except Exception as e:
        if debug_mode: st.error(f"Fetch Error: {e}")
        return []

@st.cache_data(ttl=30)
def fetch_menu(branch_name):
    try:
        ref = db.reference(f'/{branch_name}/master/menu')
        data = ref.get()
        return data if data else {}
    except: return {}

def log_activity(branch, user, action, details):
    try:
        ref = db.reference(f'/{branch}/activity_log')
        ref.push({"timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "user": user, "action": action, "details": details})
    except: pass

def update_menu_item(branch, cat, name, data):
    try: db.reference(f'/{branch}/master/menu/{cat}/{name}').update(data); return True
    except: return False

def add_menu_item(branch, cat, name, data):
    try: db.reference(f'/{branch}/master/menu/{cat}/{name}').set(data); return True
    except: return False

def delete_menu_item(branch, cat, name):
    try: db.reference(f'/{branch}/master/menu/{cat}/{name}').delete(); return True
    except: return False

# ==============================================================================
# 3. DATA PROCESSING
# ==============================================================================

def _calculate_promo_bun_sales(history_data, start_datetime, end_datetime):
    bun_sales_count = {}
    for order in history_data:
        try:
            ts = order.get('timestamp') or order.get('completed_time')
            if not ts: continue
            ot = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except: continue

        if start_datetime <= ot <= end_datetime:
            pb = order.get('promo_bun_breakdown', {})
            if pb:
                for k, v in pb.items():
                    bun_sales_count[k] = bun_sales_count.get(k, 0) + v
            else:
                items = order.get('items', [])
                if isinstance(items, dict): items = items.values()
                for item in items:
                    name = item.get('name', '').upper()
                    price = item.get('price', 0)
                    if "POLO BUN" in name and price == 11000:
                         nm = item.get('name')
                         qty = item.get('quantity', item.get('qty', 0))
                         bun_sales_count[nm] = bun_sales_count.get(nm, 0) + qty
    return bun_sales_count

def calculate_grand_total(subtotal, discount_val=0):
    subtotal = float(subtotal)
    service = subtotal * 0.05
    tax = (subtotal + service) * 0.10
    total = subtotal + service + tax - float(discount_val)
    return {'subtotal': subtotal, 'tax': tax, 'service': service, 'discount': discount_val, 'total': total}

def process_data_for_display(history_data):
    processed = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            subtotal = sum(i.get('price', 0) * i.get('quantity', i.get('qty', 1)) for i in items)
            grand_total = order.get('total_final', order.get('total', 0))
            
            ts = order.get('timestamp') or order.get('completed_time')
            if ts:
                ot = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                pay_method = order.get('payment_method', '-')
                if isinstance(pay_method, list): pay_method = ", ".join(pay_method)
                
                processed.append({
                    "Kode Unik": order.get('order_id', order.get('unique_code', 'N/A')),
                    "Tanggal": ot.date(),
                    "Waktu": ot.time(),
                    "Tipe Order": order.get('order_type', 'N/A'),
                    "Meja": order.get('table_number', 'N/A'),
                    "Kasir": order.get('void_by', order.get('cashier', 'N/A')),
                    "Grand Total": grand_total,
                    "Subtotal": subtotal,
                    "Metode Bayar": pay_method,
                    "Detail Item": "; ".join([f"{i.get('quantity', i.get('qty', 1))}x {i.get('name')}" for i in items]),
                    "Diskon": float(order.get('discount_amount', 0)),
                    "Service (5%)": float(order.get('service_charge', 0)),
                    "Pajak (10%)": float(order.get('tax_pb1', 0)),
                    "Detail Pembayaran": [{"method": pay_method, "amount": grand_total}] 
                })
        except: continue
    return pd.DataFrame(processed)

def process_data_for_analysis(history_data, menu_data):
    cat_map, main_map = {}, {}
    if isinstance(menu_data, dict):
        for c, items in menu_data.items():
            main = extract_main_category(c)
            if isinstance(items, dict):
                for k in items:
                    cat_map[k] = c
                    main_map[k] = main
    
    analysis = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            # Skip jika items kosong (dari dummy report)
            if not items: continue

            ts = order.get('timestamp')
            if ts:
                ot = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                for i in items:
                    nm = i.get('name', 'N/A')
                    analysis.append({
                        "Kode Unik": order.get('order_id', order.get('unique_code', 'N/A')),
                        "Tanggal": ot.date(),
                        "Waktu Mulai Order": ot.time(),
                        "Jam": ot.hour,
                        "Tipe Order": order.get('order_type', 'N/A'),
                        "Nama Menu": nm,
                        "Kategori (Asli)": cat_map.get(nm, 'Lain-lain'),
                        "Kategori Utama": main_map.get(nm, 'Lain-lain'),
                        "Kuantitas Terjual": i.get('quantity', i.get('qty', 1)),
                        "Harga Satuan": i.get('price', 0),
                        "Total Harga Item": i.get('quantity', i.get('qty', 1)) * i.get('price', 0)
                    })
        except: continue
    
    # [PERBAIKAN ERROR KEYERROR]
    # Jika analysis kosong (misal belum ada item terjual hari ini), 
    # return DataFrame kosong TAPI dengan kolom yang lengkap.
    if not analysis:
        return pd.DataFrame(columns=[
            "Kode Unik", "Tanggal", "Waktu Mulai Order", "Jam", "Tipe Order", 
            "Nama Menu", "Kategori (Asli)", "Kategori Utama", 
            "Kuantitas Terjual", "Harga Satuan", "Total Harga Item"
        ])

    return pd.DataFrame(analysis)

def extract_main_category(category_name):
    match = re.search(r'\(([^)]+)\)', category_name)
    return match.group(1).strip() if match else category_name.strip()

# ==============================================================================
# 4. EXCEL GENERATOR (FULL COMPATIBLE)
# ==============================================================================
def create_excel_report(df_display, df_analysis, promo_bun_data=None): 
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        wb = writer.book
        money_fmt = wb.add_format({'num_format': 'Rp #,##0'})
        header_fmt = wb.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'fg_color': '#D9E1F2', 'border': 1})
        
        # SHEET 1: RINGKASAN
        if not df_display.empty:
            ws1 = wb.add_worksheet('1. Ringkasan')
            ws1.write('A1', 'Metrik Keuangan', header_fmt); ws1.write('B1', 'Nilai', header_fmt)
            ws1.set_column('A:A', 30); ws1.set_column('B:B', 20)
            
            summ = {
                "Total Omset": df_display['Grand Total'].sum(),
                "Total Transaksi": len(df_display),
                "Rata-rata Transaksi": df_display['Grand Total'].mean(),
            }
            r=1
            for k,v in summ.items():
                ws1.write(r,0,k); ws1.write(r,1,v, money_fmt if "Total" in k or "Rata" in k else None)
                r+=1
            
            if promo_bun_data:
                r+=2
                ws1.write(r,0,"POLO BUN PROMO", header_fmt); ws1.write(r,1,"QTY", header_fmt); r+=1
                for k,v in promo_bun_data.items():
                    ws1.write(r,0,k); ws1.write(r,1,v); r+=1

        # SHEET 2: DATA TRANSAKSI
        if not df_display.empty:
            df_out = df_display.drop(columns=['Detail Pembayaran'], errors='ignore')
            df_out['Tanggal'] = df_out['Tanggal'].astype(str)
            df_out['Waktu'] = df_out['Waktu'].astype(str)
            
            df_out.to_excel(writer, index=False, sheet_name='2. Data Transaksi')
            ws2 = writer.sheets['2. Data Transaksi']
            ws2.set_column('A:Z', 18)

        # SHEET 3: ANALISA ITEM
        if not df_analysis.empty:
            df_analysis['Tanggal'] = df_analysis['Tanggal'].astype(str)
            df_analysis.to_excel(writer, index=False, sheet_name='3. Rincian Item')
            
            df_kat = df_analysis.groupby('Kategori Utama')['Total Harga Item'].sum().reset_index().sort_values('Total Harga Item', ascending=False)
            df_kat.to_excel(writer, index=False, sheet_name='4. Omset Kategori')
            
            df_menu = df_analysis.groupby('Nama Menu')['Kuantitas Terjual'].sum().reset_index().sort_values('Kuantitas Terjual', ascending=False)
            df_menu.to_excel(writer, index=False, sheet_name='5. Top Menu')

    return output

# ==============================================================================
# 5. MAIN APP
# ==============================================================================

if not st.session_state['logged_in']:
    login()
else:
    with st.sidebar:
        st.title("⚙️ Pengaturan")
        st.info(f"User: **{st.session_state['user_name']}**")
        if st.button("LOGOUT", use_container_width=True):
            logout()
        
        st.divider()
        debug_mode = st.checkbox("🔧 Mode Debug", value=False, help="Centang untuk melihat raw data Firebase.")
    
    st.title(f"📊 Dashboard Monitoring")
    initialize_firebase()

    branches = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]
    selected_branch = st.selectbox("Pilih Cabang:", branches)

    if selected_branch:
        with st.spinner("Memuat data..."):
            history_data = fetch_data(selected_branch, debug_mode)
            menu_data = fetch_menu(selected_branch)

        tab1, tab2, tab_menu = st.tabs(["📈 Ringkasan & KPI", "📄 Data Detail", "🍔 Manajemen Menu"])

        # DATA PROCESSING
        df_display = process_data_for_display(history_data)
        df_analysis = process_data_for_analysis(history_data, menu_data)
        
        # --- FILTER PERIODE ---
        st.subheader("Filter Periode")
        c1, c2 = st.columns(2)
        
        # Default date
        def_date = date.today()
        if not df_display.empty:
            min_d = df_display['Tanggal'].min()
            max_d = df_display['Tanggal'].max()
        else:
            min_d = def_date
            max_d = def_date
            
        start_date = c1.date_input("Dari", min_d)
        end_date = c2.date_input("Sampai", max_d)
        
        # [PERBAIKAN UTAMA: Filter Terpisah]
        
        # 1. Filter Data Transaksi
        if not df_display.empty:
            df_disp_fil = df_display[(df_display['Tanggal'] >= start_date) & (df_display['Tanggal'] <= end_date)]
        else:
            df_disp_fil = pd.DataFrame()

        # 2. Filter Data Analisis (Cek dulu apa kosong atau tidak)
        if not df_analysis.empty:
            df_anal_fil = df_analysis[(df_analysis['Tanggal'] >= start_date) & (df_analysis['Tanggal'] <= end_date)]
        else:
            df_anal_fil = pd.DataFrame()
        
        # Hitung Promo Bun
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        promo_bun = _calculate_promo_bun_sales(history_data, start_dt, end_dt)
        
        with tab1:
            tot = df_disp_fil['Grand Total'].sum() if not df_disp_fil.empty else 0
            trx = len(df_disp_fil) if not df_disp_fil.empty else 0
            
            k1, k2 = st.columns(2)
            k1.metric("Total Omset", f"Rp {tot:,.0f}")
            k2.metric("Total Transaksi", trx)
            
            if trx == 0 and history_data:
                st.info("Ada data 'End Day' tapi belum ada penjualan (0 transaksi).")

            if not df_disp_fil.empty:
                chart_data = df_disp_fil.groupby('Tanggal')['Grand Total'].sum().reset_index()
                st.altair_chart(alt.Chart(chart_data).mark_bar().encode(
                    x='Tanggal', y='Grand Total', tooltip=['Tanggal', 'Grand Total']
                ).interactive(), use_container_width=True) 

        with tab2:
            if df_disp_fil.empty:
                st.info("Tidak ada data transaksi detail untuk ditampilkan.")
            else:
                try:
                    st.dataframe(df_disp_fil, width=None, use_container_width=True)
                except:
                    st.dataframe(df_disp_fil)
            
            if st.button("Download Excel Laporan"):
                excel = create_excel_report(df_disp_fil, df_anal_fil, promo_bun)
                st.download_button(
                    label="Klik Disini Untuk Download",
                    data=excel.getvalue(),
                    file_name=f"Laporan_{selected_branch}_{start_date}_{end_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        # TAB 3: MENU
        with tab_menu:
            st.write(f"### Menu Editor: {selected_branch}")
            if not menu_data:
                st.warning("Data Menu Kosong. Cek path '/master/menu' di Firebase.")
            else:
                mlist = []
                for c, items in menu_data.items():
                    if isinstance(items, dict):
                        for k, v in items.items():
                            mlist.append({"Kategori": c, "Nama": k, "Harga": v.get('price',0)})
                
                df_m_view = pd.DataFrame(mlist)
                if not df_m_view.empty:
                    st.dataframe(df_m_view, use_container_width=True)
                    
                    with st.expander("✏️ Edit Harga Menu"):
                        target = st.selectbox("Pilih Menu", ["--"] + sorted([m['Nama'] for m in mlist]))
                        if target != "--":
                            ct = next(m['Kategori'] for m in mlist if m['Nama'] == target)
                            op = next(m['Harga'] for m in mlist if m['Nama'] == target)
                            np = st.number_input("Harga Baru", value=int(op), step=1000)
                            if st.button("Simpan Perubahan"):
                                update_menu_item(selected_branch, ct, target, {"price": np})
                                log_activity(selected_branch, st.session_state['user_name'], "Edit Harga", f"{target}: {np}")
                                st.success("Tersimpan!"); time.sleep(1); st.rerun()
                else:
                    st.info("Struktur menu tidak sesuai format.")
