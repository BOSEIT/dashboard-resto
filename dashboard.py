import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
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

st.set_page_config(layout="wide", page_title="Dashboard X-POS (Firestore)")

# URL Database (Tidak terlalu dipake di Firestore, tapi tetap disimpan untuk init)
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
# 2. FIREBASE FIRESTORE & DATA FETCHING
# ==============================================================================

@st.cache_resource
def initialize_firebase():
    """Inisialisasi Firebase menggunakan Service Account."""
    try:
        if not firebase_admin._apps:
            # 1. Prioritas: Cek Streamlit Secrets
            if 'firebase_credentials' in st.secrets:
                cred_info = dict(st.secrets['firebase_credentials'])
                cred = credentials.Certificate(cred_info)
                firebase_admin.initialize_app(cred)
            
            # 2. Fallback: Cek File Lokal
            else:
                cred_file = None
                # Cek beberapa kemungkinan nama file credential
                possible_files = ['serviceAccountKey.json', 'firebase-credentials.json']
                for f in possible_files:
                    if os.path.exists(f):
                        cred_file = f
                        break
                
                if cred_file:
                    cred = credentials.Certificate(cred_file)
                    firebase_admin.initialize_app(cred)
                else:
                    st.error("⚠️ FILE KUNCI (serviceAccountKey.json) TIDAK DITEMUKAN!")
                    st.stop()
    except Exception as e:
        st.error(f"Firebase Init Error: {e}"); st.stop()

def get_firestore_client():
    return firestore.client()

def fetch_data(branch_name, debug_mode=False):
    """
    Mengambil data transaksi dari Cloud Firestore.
    Path: branches/{branch_name}/daily_reports/{date_doc}
    """
    if debug_mode:
        st.cache_data.clear()
        
    try:
        db = get_firestore_client()
        # Referensi ke collection daily_reports cabang terkait
        reports_ref = db.collection('branches').document(branch_name).collection('daily_reports')
        
        # Ambil semua dokumen (semua tanggal)
        # Note: Jika data sangat banyak, sebaiknya di-limit atau filter by date range query
        docs = reports_ref.stream()
        
        all_transactions = []
        found_dates = []

        for doc in docs:
            data = doc.to_dict()
            date_key = doc.id
            found_dates.append(date_key)
            
            # Ambil list transaksi dari field 'transactions'
            trx_list = data.get('transactions', [])
            
            if trx_list and isinstance(trx_list, list):
                all_transactions.extend(trx_list)
            
            # Jika transaksi kosong tapi ada summary, buat dummy (fallback logic)
            elif 'summary' in data:
                summary = data['summary']
                if summary.get('total_sales', 0) > 0:
                    dummy_trx = {
                        "order_id": f"Z-REPORT-{date_key}",
                        "unique_code": "DAILY-CLOSE",
                        "timestamp": f"{date_key} 23:59:59", 
                        "total_final": summary.get('total_sales', 0),
                        "items": [], 
                        "status": "completed",
                        "order_type": "Laporan Harian",
                        "payment_method": "Rekap Manual",
                        "discount_amount": 0,
                        "service_charge": 0,
                        "tax_pb1": 0
                    }
                    all_transactions.append(dummy_trx)

        if debug_mode:
            st.info(f"🔍 Firestore Path: branches/{branch_name}/daily_reports")
            st.write(f"📅 Tanggal ditemukan: {len(found_dates)} hari")
            st.write(found_dates)
            
        return all_transactions

    except Exception as e:
        if debug_mode: st.error(f"Fetch Error: {e}")
        return []

@st.cache_data(ttl=60) # Cache 60 detik
def fetch_menu(branch_name):
    """
    Mengambil Master Menu dari laporan terakhir yang diupload ke Firestore.
    Karna kita menyertakan 'master_data' di setiap upload Z-Report.
    """
    try:
        db = get_firestore_client()
        # Query 1 dokumen terakhir berdasarkan tanggal (descending)
        docs = db.collection('branches').document(branch_name).collection('daily_reports')\
                 .order_by('date', direction=firestore.Query.DESCENDING).limit(1).stream()
        
        for doc in docs:
            data = doc.to_dict()
            # Ambil master_data -> menu
            return data.get('master_data', {}).get('menu', {})
            
        return {}
    except: return {}

# NOTE: Fungsi Edit Menu dinonaktifkan sementara karena dashboard ini Read-Only dari Cloud
# POS lokal adalah sumber kebenaran (Source of Truth).
def log_activity(branch, user, action, details):
    pass 

def update_menu_item(branch, cat, name, data):
    st.warning("Fitur edit dimatikan. Silakan edit menu dari Aplikasi Kasir (POS) Local.")
    return False

# ==============================================================================
# 3. DATA PROCESSING
# ==============================================================================

def _calculate_promo_bun_sales(history_data, start_datetime, end_datetime):
    bun_sales_count = {}
    for order in history_data:
        try:
            ts = order.get('timestamp') or order.get('completed_time')
            if not ts: continue
            try:
                ot = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            except:
                # Fallback jika format timestamp berbeda (misal dari Firestore timestamp object)
                ot = datetime.fromisoformat(str(ts).replace('Z', ''))
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

def process_data_for_display(history_data):
    processed = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            subtotal = sum(float(i.get('price', 0)) * float(i.get('quantity', i.get('qty', 1))) for i in items)
            grand_total = float(order.get('total_final', order.get('total', 0)))
            
            ts = order.get('timestamp') or order.get('completed_time')
            if ts:
                try:
                    ot = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except: continue # Skip invalid date format

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
        except Exception as e: 
            # print(f"Skip Error: {e}")
            continue
    return pd.DataFrame(processed)

def process_data_for_analysis(history_data, menu_data):
    cat_map, main_map = {}, {}
    # Flatten menu data untuk mapping kategori
    if isinstance(menu_data, dict):
        for c, items in menu_data.items():
            main = extract_main_category(c)
            if isinstance(items, dict):
                for k in items:
                    cat_map[k] = c
                    main_map[k] = main
            elif isinstance(items, list): # Handle jika menu format list
                 for m_item in items:
                     if isinstance(m_item, dict):
                         nm = m_item.get('name')
                         if nm:
                             cat_map[nm] = c
                             main_map[nm] = main

    analysis = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            if not items: continue

            ts = order.get('timestamp')
            if ts:
                try:
                    ot = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                except: continue

                for i in items:
                    nm = i.get('name', 'N/A')
                    qty = float(i.get('quantity', i.get('qty', 1)))
                    price = float(i.get('price', 0))
                    
                    analysis.append({
                        "Kode Unik": order.get('order_id', order.get('unique_code', 'N/A')),
                        "Tanggal": ot.date(),
                        "Waktu Mulai Order": ot.time(),
                        "Jam": ot.hour,
                        "Tipe Order": order.get('order_type', 'N/A'),
                        "Nama Menu": nm,
                        "Kategori (Asli)": cat_map.get(nm, 'Lain-lain'),
                        "Kategori Utama": main_map.get(nm, 'Lain-lain'),
                        "Kuantitas Terjual": qty,
                        "Harga Satuan": price,
                        "Total Harga Item": qty * price
                    })
        except: continue
    
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
        debug_mode = st.checkbox("🔧 Mode Debug", value=False, help="Centang untuk melihat raw data Firestore.")
    
    st.title(f"📊 Dashboard Monitoring (Cloud)")
    initialize_firebase()

    # Daftar Cabang (Sesuaikan dengan nama dokumen di Collection 'branches' di Firestore)
    branches = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]
    selected_branch = st.selectbox("Pilih Cabang:", branches)

    if selected_branch:
        with st.spinner("Memuat data dari Cloud Firestore..."):
            history_data = fetch_data(selected_branch, debug_mode)
            menu_data = fetch_menu(selected_branch)

        tab1, tab2, tab_menu = st.tabs(["📈 Ringkasan & KPI", "📄 Data Detail", "🍔 Lihat Menu"])

        # DATA PROCESSING
        df_display = process_data_for_display(history_data)
        df_analysis = process_data_for_analysis(history_data, menu_data)
        
        # --- FILTER PERIODE ---
        st.subheader("Filter Periode")
        c1, c2 = st.columns(2)
        
        # Default date logic
        def_date = date.today()
        if not df_display.empty:
            try:
                min_d = df_display['Tanggal'].min()
                max_d = df_display['Tanggal'].max()
            except:
                min_d = def_date
                max_d = def_date
        else:
            min_d = def_date
            max_d = def_date
            
        start_date = c1.date_input("Dari", min_d)
        end_date = c2.date_input("Sampai", max_d)
        
        # Filter Logic
        if not df_display.empty:
            df_disp_fil = df_display[(df_display['Tanggal'] >= start_date) & (df_display['Tanggal'] <= end_date)]
        else:
            df_disp_fil = pd.DataFrame()

        if not df_analysis.empty:
            df_anal_fil = df_analysis[(df_analysis['Tanggal'] >= start_date) & (df_analysis['Tanggal'] <= end_date)]
        else:
            df_anal_fil = pd.DataFrame()
        
        # Promo Bun Calculation
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
        promo_bun = _calculate_promo_bun_sales(history_data, start_dt, end_dt)
        
        with tab1:
            tot = df_disp_fil['Grand Total'].sum() if not df_disp_fil.empty else 0
            trx = len(df_disp_fil) if not df_disp_fil.empty else 0
            
            k1, k2 = st.columns(2)
            k1.metric("Total Omset", f"Rp {tot:,.0f}")
            k2.metric("Total Transaksi", trx)
            
            if trx == 0:
                if history_data:
                    st.info("Data ditemukan, tapi tidak ada transaksi di rentang tanggal ini.")
                else:
                    st.warning("Data kosong di Cloud untuk cabang ini.")

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

        # TAB 3: MENU (READ ONLY)
        with tab_menu:
            st.write(f"### Menu Terakhir ({selected_branch})")
            st.caption("Data menu diambil dari upload End-Day terakhir.")
            
            if not menu_data:
                st.warning("Data Menu belum tersedia di Cloud. Lakukan 'End Day' di POS setidaknya sekali.")
            else:
                mlist = []
                # Handle variasi struktur menu
                for c, items in menu_data.items():
                    if isinstance(items, dict):
                        for k, v in items.items():
                            mlist.append({"Kategori": c, "Nama": k, "Harga": v.get('price',0)})
                    elif isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                mlist.append({"Kategori": c, "Nama": item.get('name'), "Harga": item.get('price',0)})

                df_m_view = pd.DataFrame(mlist)
                if not df_m_view.empty:
                    st.dataframe(df_m_view, use_container_width=True)
                else:
                    st.info("Format menu tidak dikenali.")
