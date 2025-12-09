import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore
from io import BytesIO
from datetime import datetime, date, time as dt_time
import altair as alt
import xlsxwriter
import re
import time
import os
import json 

# ==============================================================================
# 1. KONFIGURASI & LOGIN
# ==============================================================================

st.set_page_config(layout="wide", page_title="Dashboard X-POS (Enterprise)")

# URL Database
FIREBASE_DB_URL = 'https://xpos.asia-southeast1.firebasedatabase.app'

# Data User Login
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
            if 'firebase_credentials' in st.secrets:
                cred_info = dict(st.secrets['firebase_credentials'])
                cred = credentials.Certificate(cred_info)
                firebase_admin.initialize_app(cred)
            else:
                cred_file = None
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
    """Mengambil data transaksi dari Cloud Firestore."""
    if debug_mode:
        st.cache_data.clear()
        
    try:
        db = get_firestore_client()
        reports_ref = db.collection('branches').document(branch_name).collection('daily_reports')
        docs = reports_ref.stream()
        
        all_transactions = []
        for doc in docs:
            data = doc.to_dict()
            date_key = doc.id
            trx_list = data.get('transactions', [])
            
            if trx_list and isinstance(trx_list, list):
                all_transactions.extend(trx_list)
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
                        "payment_method": "Rekap Manual"
                    }
                    all_transactions.append(dummy_trx)
            
        return all_transactions
    except Exception as e:
        if debug_mode: st.error(f"Fetch Error: {e}")
        return []

# --- MENU MANAGEMENT FUNCTIONS ---

def fetch_menu_config(branch_name):
    """Mengambil konfigurasi menu."""
    db = get_firestore_client()
    try:
        config_ref = db.collection('branches').document(branch_name).collection('configuration').document('menu')
        doc = config_ref.get()
        if doc.exists:
            data = doc.to_dict()
            return data.get('items', {})
        
        # Fallback
        docs = db.collection('branches').document(branch_name).collection('daily_reports')\
                 .order_by('date', direction=firestore.Query.DESCENDING).limit(1).stream()
        for d in docs:
            return d.to_dict().get('master_data', {}).get('menu', {})
        
        return {} 
    except Exception as e:
        st.error(f"Gagal ambil data menu: {e}")
        return {}

def save_menu_config_to_cloud(branch_name, new_menu_data):
    """Menyimpan konfigurasi menu ke Firestore."""
    try:
        db = get_firestore_client()
        config_ref = db.collection('branches').document(branch_name).collection('configuration').document('menu')
        
        payload = {
            "last_updated": firestore.SERVER_TIMESTAMP,
            "updated_by": st.session_state.get('user_name', 'Admin'),
            "items": new_menu_data
        }
        
        config_ref.set(payload)
        return True, "Menu berhasil disimpan ke Cloud! Jangan lupa download di POS."
    except Exception as e:
        return False, f"Gagal simpan: {e}"

# ==============================================================================
# 3. DATA PROCESSING
# ==============================================================================

def parse_flexible_date(ts):
    if not ts: return None
    if hasattr(ts, 'date'): return ts
    ts_str = str(ts)
    try: return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try: return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except ValueError:
            try:
                d = datetime.strptime(ts_str, "%Y-%m-%d").date()
                return datetime.combine(d, dt_time(0,0,0))
            except: return None

def process_data_for_display(history_data):
    """Memproses data untuk tampilan tabel transaksi & perhitungan omset global."""
    processed = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            grand_total = float(order.get('total_final', order.get('total', 0)))
            # Coba ambil field detail lain jika ada (untuk akurasi laporan)
            subtotal = float(order.get('subtotal', 0))
            if subtotal == 0 and grand_total > 0: subtotal = grand_total # Fallback
            
            tax = float(order.get('tax_pb1', 0))
            svc = float(order.get('service_charge', 0))
            disc = float(order.get('discount_amount', 0))
            
            ts = order.get('timestamp') or order.get('completed_time')
            ot = parse_flexible_date(ts)

            if ot:
                if ot.tzinfo is not None: ot = ot.replace(tzinfo=None)
                pay_method = order.get('payment_method', '-')
                if isinstance(pay_method, list): pay_method = ", ".join(pay_method)
                
                processed.append({
                    "Kode Unik": order.get('order_id', order.get('unique_code', 'N/A')),
                    "Tanggal": ot.date(),
                    "Waktu": ot.time(),
                    "Jam": ot.hour, # Untuk analisa per jam
                    "Tipe Order": order.get('order_type', 'N/A'),
                    "Meja": order.get('table_number', 'N/A'),
                    "Subtotal": subtotal,
                    "Diskon": disc,
                    "Service": svc,
                    "Tax": tax,
                    "Grand Total": grand_total,
                    "Metode Bayar": pay_method,
                    "Kasir": order.get('cashier', 'System'),
                    "Detail Item": "; ".join([f"{i.get('quantity', i.get('qty', 1))}x {i.get('name')}" for i in items]),
                })
        except: continue
    return pd.DataFrame(processed)

def process_data_for_analysis(history_data, menu_data):
    """Memproses data level Item untuk analisa kategori dan produk terlaris."""
    cat_map = {}
    if isinstance(menu_data, dict):
        for c, items in menu_data.items():
            if isinstance(items, dict):
                 for k in items: cat_map[k] = c
            elif isinstance(items, list): 
                 for m_item in items:
                     if isinstance(m_item, dict):
                         nm = m_item.get('name')
                         if nm: cat_map[nm] = c

    analysis = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            ts = order.get('timestamp') or order.get('completed_time')
            ot = parse_flexible_date(ts)
            
            if ot:
                if ot.tzinfo is not None: ot = ot.replace(tzinfo=None)
                for i in items:
                    nm = i.get('name', 'N/A')
                    qty = float(i.get('quantity', i.get('qty', 1)))
                    price = float(i.get('price', 0))
                    
                    analysis.append({
                        "Tanggal": ot.date(),
                        "Nama Menu": nm,
                        "Kategori": cat_map.get(nm, 'Lain-lain'),
                        "Qty": qty,
                        "Harga Satuan": price,
                        "Total": qty * price
                    })
        except: continue
    return pd.DataFrame(analysis)

# ==============================================================================
# 4. EXCEL REPORT GENERATOR (ESB STYLE - ENTERPRISE GRADE)
# ==============================================================================
def create_esb_style_excel(df_trx, df_items, branch_name, start_date, end_date): 
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # --- DEFINISI FORMATTING (Styling) ---
        # Header Utama
        fmt_title = workbook.add_format({'bold': True, 'font_size': 14, 'align': 'left'})
        fmt_subtitle = workbook.add_format({'italic': True, 'font_size': 10, 'align': 'left', 'font_color': '#555555'})
        
        # Table Header
        fmt_th = workbook.add_format({
            'bold': True, 'align': 'center', 'valign': 'vcenter', 
            'bg_color': '#2C3E50', 'font_color': '#FFFFFF', 'border': 1
        })
        
        # Cells
        fmt_text = workbook.add_format({'border': 1, 'align': 'left'})
        fmt_center = workbook.add_format({'border': 1, 'align': 'center'})
        fmt_num = workbook.add_format({'border': 1, 'align': 'center'})
        fmt_curr = workbook.add_format({'border': 1, 'num_format': 'Rp #,##0', 'align': 'right'})
        
        # Totals
        fmt_total_label = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#ECF0F1', 'align': 'right'})
        fmt_total_val = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#ECF0F1', 'num_format': 'Rp #,##0', 'align': 'right'})

        # --- SHEET 1: SALES SUMMARY (RINGKASAN EKSEKUTIF) ---
        ws = workbook.add_worksheet('Sales Summary')
        ws.set_column('A:A', 30)
        ws.set_column('B:B', 20)
        
        # Judul Laporan
        ws.write('A1', f"SALES SUMMARY REPORT - {branch_name}", fmt_title)
        ws.write('A2', f"Periode: {start_date} s/d {end_date}", fmt_subtitle)
        
        if not df_trx.empty:
            # Hitung Metrics
            total_bill = len(df_trx)
            gross_sales = df_trx['Subtotal'].sum()
            total_disc = df_trx['Diskon'].sum()
            total_svc = df_trx['Service'].sum()
            total_tax = df_trx['Tax'].sum()
            net_sales = df_trx['Grand Total'].sum()
            avg_bill = net_sales / total_bill if total_bill > 0 else 0

            # Tabel Ringkasan Keuangan
            row = 4
            ws.write(row, 0, "METRICS", fmt_th)
            ws.write(row, 1, "AMOUNT", fmt_th)
            row += 1
            
            kpis = [
                ("Gross Sales (Subtotal)", gross_sales),
                ("(-) Total Discount", total_disc),
                ("(+) Service Charge", total_svc),
                ("(+) Tax (PB1)", total_tax),
                ("(=) NET SALES", net_sales),
            ]
            
            for k, v in kpis:
                ws.write(row, 0, k, fmt_text)
                ws.write(row, 1, v, fmt_curr)
                row += 1
                
            # Statistik Tambahan
            row += 1
            ws.write(row, 0, "Total Transactions", fmt_text)
            ws.write(row, 1, total_bill, fmt_center)
            row += 1
            ws.write(row, 0, "Average per Bill", fmt_text)
            ws.write(row, 1, avg_bill, fmt_curr)

        # --- SHEET 2: PAYMENT REPORT (PEMBAYARAN) ---
        ws_pay = workbook.add_worksheet('Payment Report')
        ws_pay.set_column('A:A', 25)
        ws_pay.set_column('B:B', 20)
        ws_pay.set_column('C:C', 15)
        
        ws_pay.write('A1', "PAYMENT METHOD REPORT", fmt_title)
        
        if not df_trx.empty:
            ws_pay.write('A3', "Payment Method", fmt_th)
            ws_pay.write('B3', "Total Amount", fmt_th)
            ws_pay.write('C3', "Trans. Count", fmt_th)
            
            pay_sum = df_trx.groupby('Metode Bayar').agg({'Grand Total': 'sum', 'Kode Unik': 'count'}).reset_index()
            r = 3
            for idx, row_data in pay_sum.iterrows():
                ws_pay.write(r, 0, row_data['Metode Bayar'], fmt_text)
                ws_pay.write(r, 1, row_data['Grand Total'], fmt_curr)
                ws_pay.write(r, 2, row_data['Kode Unik'], fmt_center)
                r += 1
            
            # Total Bawah
            ws_pay.write(r, 0, "TOTAL", fmt_total_label)
            ws_pay.write(r, 1, pay_sum['Grand Total'].sum(), fmt_total_val)
            ws_pay.write(r, 2, pay_sum['Kode Unik'].sum(), fmt_total_val)

        # --- SHEET 3: CATEGORY SALES (KATEGORI) ---
        if not df_items.empty:
            ws_cat = workbook.add_worksheet('Category Sales')
            ws_cat.set_column('A:A', 25)
            ws_cat.set_column('B:B', 20)
            ws_cat.set_column('C:C', 15)
            
            ws_cat.write('A1', "SALES BY CATEGORY", fmt_title)
            
            cat_sum = df_items.groupby('Kategori').agg({'Total': 'sum', 'Qty': 'sum'}).reset_index().sort_values('Total', ascending=False)
            
            ws_cat.write('A3', "Category Name", fmt_th)
            ws_cat.write('B3', "Total Sales", fmt_th)
            ws_cat.write('C3', "Total Qty", fmt_th)
            
            r = 3
            for idx, row_data in cat_sum.iterrows():
                ws_cat.write(r, 0, row_data['Kategori'], fmt_text)
                ws_cat.write(r, 1, row_data['Total'], fmt_curr)
                ws_cat.write(r, 2, row_data['Qty'], fmt_center)
                r += 1
                
            ws_cat.write(r, 0, "TOTAL", fmt_total_label)
            ws_cat.write(r, 1, cat_sum['Total'].sum(), fmt_total_val)
            ws_cat.write(r, 2, cat_sum['Qty'].sum(), fmt_total_val)

        # --- SHEET 4: ITEM SALES (DETAIL MENU) ---
        if not df_items.empty:
            ws_item = workbook.add_worksheet('Item Sales')
            ws_item.set_column('A:A', 20) # Kategori
            ws_item.set_column('B:B', 30) # Nama Item
            ws_item.set_column('C:C', 10) # Qty
            ws_item.set_column('D:D', 15) # Harga Satuan
            ws_item.set_column('E:E', 20) # Total
            
            ws_item.write('A1', "PRODUCT MIX REPORT (ITEM SALES)", fmt_title)
            
            # Group by Item agar tidak duplikat
            item_sum = df_items.groupby(['Kategori', 'Nama Menu']).agg({'Qty': 'sum', 'Total': 'sum'}).reset_index().sort_values(['Kategori', 'Total'], ascending=[True, False])
            
            headers = ["Category", "Item Name", "Qty Sold", "Total Sales"]
            for col_num, h in enumerate(headers):
                ws_item.write(2, col_num, h, fmt_th)
            
            r = 3
            for idx, row_data in item_sum.iterrows():
                ws_item.write(r, 0, row_data['Kategori'], fmt_text)
                ws_item.write(r, 1, row_data['Nama Menu'], fmt_text)
                ws_item.write(r, 2, row_data['Qty'], fmt_center)
                ws_item.write(r, 3, row_data['Total'], fmt_curr)
                r += 1

        # --- SHEET 5: HOURLY SALES (JAM SIBUK) ---
        if not df_trx.empty:
            ws_hour = workbook.add_worksheet('Hourly Sales')
            ws_hour.set_column('A:A', 15)
            ws_hour.set_column('B:B', 20)
            ws_hour.set_column('C:C', 15)
            
            ws_hour.write('A1', "HOURLY SALES TREND", fmt_title)
            
            # Group by Jam
            hour_sum = df_trx.groupby('Jam').agg({'Grand Total': 'sum', 'Kode Unik': 'count'}).reset_index().sort_values('Jam')
            
            ws_hour.write('A3', "Hour", fmt_th)
            ws_hour.write('B3', "Total Sales", fmt_th)
            ws_hour.write('C3', "Trans. Count", fmt_th)
            
            r = 3
            for idx, row_data in hour_sum.iterrows():
                jam_str = f"{int(row_data['Jam']):02d}:00 - {int(row_data['Jam'])+1:02d}:00"
                ws_hour.write(r, 0, jam_str, fmt_center)
                ws_hour.write(r, 1, row_data['Grand Total'], fmt_curr)
                ws_hour.write(r, 2, row_data['Kode Unik'], fmt_center)
                r += 1
                
            # Chart Sederhana di Excel
            chart = workbook.add_chart({'type': 'column'})
            chart.add_series({
                'name':       'Sales Amount',
                'categories': ['Hourly Sales', 3, 0, r-1, 0],
                'values':     ['Hourly Sales', 3, 1, r-1, 1],
                'fill':       {'color': '#3498DB'}
            })
            chart.set_title ({'name': 'Hourly Sales Performance'})
            chart.set_x_axis({'name': 'Hour'})
            chart.set_y_axis({'name': 'Sales (Rp)'})
            ws_hour.insert_chart('E3', chart)

        # --- SHEET 6: TRANSACTION LOG (RAW DATA) ---
        if not df_trx.empty:
            ws_log = workbook.add_worksheet('Transaction Log')
            
            # Kolom yang akan ditampilkan
            columns = ['Kode Unik', 'Tanggal', 'Waktu', 'Meja', 'Kasir', 'Tipe Order', 'Metode Bayar', 'Grand Total', 'Detail Item']
            
            # Header
            for i, col in enumerate(columns):
                ws_log.write(0, i, col, fmt_th)
                # Set lebar kolom manual biar rapi
                width = 15
                if col == 'Detail Item': width = 50
                elif col == 'Kode Unik': width = 20
                ws_log.set_column(i, i, width)

            # Data
            r = 1
            for idx, row_data in df_trx.iterrows():
                for c_idx, col_name in enumerate(columns):
                    val = row_data[col_name]
                    # Format khusus
                    cell_fmt = fmt_text
                    if col_name == 'Grand Total': cell_fmt = fmt_curr
                    elif col_name in ['Tanggal', 'Waktu']: val = str(val); cell_fmt = fmt_center
                    elif col_name in ['Meja', 'Kasir']: cell_fmt = fmt_center
                    
                    ws_log.write(r, c_idx, val, cell_fmt)
                r += 1

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
        if st.button("LOGOUT", use_container_width=True): logout()
        st.divider()
        debug_mode = st.checkbox("🔧 Mode Debug", value=False)
    
    st.title(f"📊 Dashboard Monitoring (Enterprise)")
    initialize_firebase()

    branches = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]
    selected_branch = st.selectbox("Pilih Cabang:", branches)

    if selected_branch:
        with st.spinner("Memuat data dari Cloud Firestore..."):
            history_data = fetch_data(selected_branch, debug_mode)
            current_menu_config = fetch_menu_config(selected_branch)

        # TAB DEFINITION
        tab1, tab2, tab_menu_view, tab_menu_edit = st.tabs([
            "📈 Ringkasan & KPI", "📄 Data Detail (Export)", "🍔 Lihat Menu (View)", "📝 Editor Menu (Admin)"
        ])

        # PROSES DATA
        df_display = process_data_for_display(history_data)
        df_analysis = process_data_for_analysis(history_data, current_menu_config)
        
        # --- TAB 1: RINGKASAN & ANALISA ---
        with tab1:
            st.subheader("📊 Analisa Bisnis")
            if not df_display.empty:
                min_date = df_display['Tanggal'].min(); max_date = df_display['Tanggal'].max()
            else:
                min_date = date.today(); max_date = date.today()
                
            c1, c2 = st.columns(2)
            d1 = c1.date_input("Dari Tanggal", min_date)
            d2 = c2.date_input("Sampai Tanggal", max_date)
            
            if not df_display.empty:
                mask_display = (df_display['Tanggal'] >= d1) & (df_display['Tanggal'] <= d2)
                df_filtered = df_display[mask_display]
                
                if not df_analysis.empty:
                    mask_analysis = (df_analysis['Tanggal'] >= d1) & (df_analysis['Tanggal'] <= d2)
                    df_filtered_analysis = df_analysis[mask_analysis]
                else:
                    df_filtered_analysis = pd.DataFrame()
                
                # KPI Cards
                tot = df_filtered['Grand Total'].sum()
                trx = len(df_filtered)
                avg_basket = tot / trx if trx > 0 else 0
                
                k1, k2, k3 = st.columns(3)
                k1.metric("Total Omset", f"Rp {tot:,.0f}")
                k2.metric("Total Transaksi", f"{trx} Bon")
                k3.metric("Rata-rata per Bon", f"Rp {avg_basket:,.0f}")
                
                st.divider()
                
                # Charts
                col_c1, col_c2 = st.columns([2, 1])
                with col_c1:
                    st.write("##### 📈 Tren Penjualan Harian")
                    daily_chart = df_filtered.groupby('Tanggal')['Grand Total'].sum().reset_index()
                    st.altair_chart(alt.Chart(daily_chart).mark_line(point=True).encode(
                        x='Tanggal', y='Grand Total', tooltip=['Tanggal', 'Grand Total']
                    ).interactive(), use_container_width=True)
                
                with col_c2:
                    st.write("##### 🍩 Proporsi Kategori (Rp)")
                    if not df_filtered_analysis.empty:
                        cat_chart = df_filtered_analysis.groupby('Kategori')['Total'].sum().reset_index()
                        base = alt.Chart(cat_chart).encode(theta=alt.Theta("Total", stack=True))
                        pie = base.mark_arc(outerRadius=120).encode(
                            color=alt.Color("Kategori"),
                            order=alt.Order("Total", sort="descending"),
                            tooltip=["Kategori", "Total"]
                        )
                        st.altair_chart(pie, use_container_width=True)
                
                st.write("##### 🏆 Top 5 Menu Terlaris (Qty)")
                if not df_filtered_analysis.empty:
                    top_menu = df_filtered_analysis.groupby('Nama Menu')['Qty'].sum().reset_index()\
                               .sort_values('Qty', ascending=False).head(5)
                    st.altair_chart(alt.Chart(top_menu).mark_bar().encode(
                        x=alt.X('Qty', title='Terjual'),
                        y=alt.Y('Nama Menu', sort='-x'),
                        tooltip=['Nama Menu', 'Qty'],
                        color=alt.value("#FF8C00") 
                    ).interactive(), use_container_width=True)
            else:
                st.info("Belum ada data transaksi di sistem.")

        # --- TAB 2: DETAIL & EXPORT (THE BIG UPDATE) ---
        with tab2:
            st.subheader("📄 Laporan Detail & Export")
            if df_display.empty: 
                st.info("Data kosong.")
            else:
                st.write("Data transaksi detail (Preview):")
                st.dataframe(df_display, use_container_width=True)
                
                st.divider()
                st.write("### 📥 Download Laporan Lengkap")
                st.info("Laporan Excel ini berisi: Sales Summary, Payment Report, Category Sales, Item Sales, Hourly Trend, dan Transaction Log.")
                
                col_btn, col_dummy = st.columns([1, 2])
                with col_btn:
                    # Filter data lagi untuk export (gunakan filter tanggal dari Tab 1 atau ambil semua?) 
                    # Idealnya ambil sesuai filter yang aktif di Tab 1, mari kita gunakan logika filter yang sama
                    # Agar konsisten, kita ambil df_filtered dan df_filtered_analysis yang sudah difilter di atas
                    # Note: df_filtered didefinisikan di Tab 1 local scope.
                    # Kita re-apply filter di sini untuk amannya.
                    
                    min_d = df_display['Tanggal'].min()
                    max_d = df_display['Tanggal'].max()
                    
                    # Kita pakai date picker di Tab 1 saja biar global, tapi karena scope streamit
                    # Kita anggap user sudah filter di Tab 1. Tapi karena variable ada di scope 'with tab1', kita harus bikin global.
                    # Solusi cepat: Re-create filter dates di sidebar atau biarkan user download ALL DATA or use Session State.
                    # Untuk kesederhanaan skrip "Full", kita export SESUAI TAMPILAN (df_filtered kalau ada, kalau tidak semua).
                    
                    # Logic: Jika variable 'df_filtered' exists (user buka tab 1), pakai itu. Jika tidak, pakai semua.
                    try:
                        data_to_export_trx = df_filtered
                        data_to_export_items = df_filtered_analysis
                        export_label = f"Laporan_{selected_branch}_{d1}_{d2}.xlsx"
                        st.success(f"Siap download untuk periode: {d1} s/d {d2}")
                    except:
                        data_to_export_trx = df_display
                        data_to_export_items = df_analysis
                        export_label = f"Laporan_{selected_branch}_ALL.xlsx"
                        st.warning("Men-download SEMUA data (Filter tanggal ada di Tab 1).")

                    if st.button("Download Excel (ESB Style)"):
                        with st.spinner("Generating Report..."):
                            excel_file = create_esb_style_excel(data_to_export_trx, data_to_export_items, selected_branch, str(min_d), str(max_d))
                            st.download_button(
                                label="📥 Klik Disini Untuk Simpan File",
                                data=excel_file.getvalue(),
                                file_name=export_label,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )

        # --- TAB 3: LIHAT MENU ---
        with tab_menu_view:
            st.subheader(f"Daftar Menu Aktif - {selected_branch}")
            view_data = []
            if current_menu_config:
                for category, items in current_menu_config.items():
                    if isinstance(items, dict):
                        for k, v in items.items():
                            view_data.append({
                                "Kategori": category, "Nama Menu": k,
                                "Harga": float(v.get('price', 0)), "Harga Online": float(v.get('online_price', 0)),
                                "Printer": v.get('printer', 'KITCHEN')
                            })
                    elif isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                view_data.append({
                                    "Kategori": category, "Nama Menu": item.get('name', ''),
                                    "Harga": float(item.get('price', 0)), "Harga Online": float(item.get('online_price', 0)),
                                    "Printer": item.get('printer', 'KITCHEN')
                                })
            
            if view_data:
                df_view = pd.DataFrame(view_data).sort_values(by=["Kategori", "Nama Menu"])
                st.dataframe(df_view, use_container_width=True, hide_index=True, column_config={"Harga": st.column_config.NumberColumn(format="Rp %d"), "Harga Online": st.column_config.NumberColumn(format="Rp %d")})
            else:
                st.info("Data menu belum tersedia.")

        # --- TAB 4: EDITOR MENU ---
        with tab_menu_edit:
            st.subheader(f"🛠️ Editor Menu - {selected_branch}")
            st.info("Edit menu di bawah ini. 'Online Price' sudah ditambahkan.")
            
            edit_data = []
            known_categories = set()
            default_categories = ["FOOD", "BEVERAGE", "SNACK", "OTHERS", "PAKET", "APPETIZER (FOOD)", "MAIN COURSE (FOOD)"]

            if current_menu_config:
                for category, items in current_menu_config.items():
                    known_categories.add(category)
                    if isinstance(items, dict):
                        for k, v in items.items():
                             edit_data.append({"Kategori": category, "Nama Menu": k, "Harga": float(v.get('price', 0)), "Harga Online": float(v.get('online_price', 0)), "Printer": v.get('printer', 'KITCHEN')})
                    elif isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                edit_data.append({"Kategori": category, "Nama Menu": item.get('name', ''), "Harga": float(item.get('price', 0)), "Harga Online": float(item.get('online_price', 0)), "Printer": item.get('printer', 'KITCHEN')})

            all_cat_options = list(known_categories.union(set(default_categories)))
            all_cat_options.sort()
            if not edit_data: edit_data.append({"Kategori": "APPETIZER (FOOD)", "Nama Menu": "CALAMARI", "Harga": 48000, "Harga Online": 57600, "Printer": "KITCHEN"})

            df_editor_source = pd.DataFrame(edit_data)
            edited_df = st.data_editor(
                df_editor_source, num_rows="dynamic", use_container_width=True, hide_index=True, column_order=["Kategori", "Nama Menu", "Harga", "Harga Online", "Printer"],
                column_config={
                    "Kategori": st.column_config.SelectboxColumn("Kategori", width="medium", options=all_cat_options, required=True),
                    "Nama Menu": st.column_config.TextColumn("Nama Menu", width="large", required=True),
                    "Harga": st.column_config.NumberColumn("Harga (Rp)", format="%d", min_value=0, step=500, width="small", required=True),
                    "Harga Online": st.column_config.NumberColumn("Harga Online (Rp)", format="%d", min_value=0, step=500, width="small", required=True),
                    "Printer": st.column_config.SelectboxColumn("Target Printer", width="medium", options=["KITCHEN", "BAR", "CASHIER", "PASTRY"], required=True)
                }
            )

            if st.button("💾 Simpan Perubahan ke Cloud", type="primary"):
                new_menu_dict = {}
                try:
                    for index, row in edited_df.iterrows():
                        cat = row['Kategori'].strip() if row['Kategori'] else "OTHERS"
                        name = str(row['Nama Menu']).strip()
                        price = float(row['Harga'])
                        online_price = float(row['Harga Online'])
                        printer = row['Printer']
                        if not name: continue
                        if cat not in new_menu_dict: new_menu_dict[cat] = {}
                        new_menu_dict[cat][name] = {"price": price, "online_price": online_price, "printer": printer}
                    
                    with st.spinner("Menyimpan ke Cloud..."):
                        success, msg = save_menu_config_to_cloud(selected_branch, new_menu_dict)
                    if success:
                        st.success(f"✅ {msg}")
                        time.sleep(1.5)
                        st.rerun()
                    else:
                        st.error(f"❌ {msg}")
                except Exception as e:
                    st.error(f"Error: {e}")
