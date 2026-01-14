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
# 1. KONFIGURASI HALAMAN
# ==============================================================================

st.set_page_config(layout="wide", page_title="Dashboard X-POS (Enterprise)")

# Master List Cabang (Untuk Pilihan Dropdown)
ALL_BRANCHES_MASTER = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]

# ==============================================================================
# 2. FIREBASE AUTH & USER MANAGEMENT (NEW)
# ==============================================================================

@st.cache_resource
def initialize_firebase():
    """Inisialisasi Firebase & Auto-Create Admin jika belum ada."""
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
        
        # --- AUTO CREATE ADMIN IF NOT EXISTS ---
        db = firestore.client()
        # Cek apakah user admin sudah ada
        admin_ref = db.collection('users').document('admin')
        if not admin_ref.get().exists:
            # Jika belum ada, buat user default
            default_admin = {
                "pin": "123",
                "role": "administrator",
                "access_branches": ["ALL"], # Bisa akses semua
                "created_at": firestore.SERVER_TIMESTAMP
            }
            admin_ref.set(default_admin)
            print("INFO: User 'admin' default berhasil dibuat.")
            
    except Exception as e:
        st.error(f"Firebase Init Error: {e}"); st.stop()

def get_firestore_client():
    return firestore.client()

def authenticate_user(username, pin):
    """Cek validitas user dan ambil data role-nya."""
    db = get_firestore_client()
    try:
        # Cari dokumen user berdasarkan ID (username)
        doc = db.collection('users').document(username).get()
        if doc.exists:
            data = doc.to_dict()
            if data.get('pin') == pin:
                return True, data
            else:
                return False, "PIN Salah!"
        else:
            return False, "Username tidak ditemukan."
    except Exception as e:
        return False, f"Error Database: {e}"

def add_new_user_to_db(new_username, new_pin, role, branches):
    """Fitur Admin untuk menambah user baru."""
    db = get_firestore_client()
    try:
        doc_ref = db.collection('users').document(new_username)
        if doc_ref.get().exists:
            return False, "Username sudah dipakai!"
        
        payload = {
            "pin": new_pin,
            "role": role,
            "access_branches": branches, # List: ["COLEGA_PIK", ...] atau ["ALL"]
            "created_at": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(payload)
        return True, f"User {new_username} berhasil dibuat."
    except Exception as e:
        return False, str(e)

def delete_user_from_db(username):
    """Fitur Admin untuk hapus user."""
    db = get_firestore_client()
    try:
        db.collection('users').document(username).delete()
        return True
    except Exception as e:
        return False

def get_all_users():
    """Ambil list semua user untuk ditampilkan ke Admin."""
    db = get_firestore_client()
    users = []
    docs = db.collection('users').stream()
    for doc in docs:
        d = doc.to_dict()
        d['username'] = doc.id
        users.append(d)
    return users

# ==============================================================================
# 3. LOGIN & SESSION UI
# ==============================================================================

# Init Session
if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'user_name' not in st.session_state:
    st.session_state['user_name'] = ""
if 'user_role' not in st.session_state:
    st.session_state['user_role'] = ""
if 'user_branches' not in st.session_state:
    st.session_state['user_branches'] = []

def login_page():
    st.markdown("<style>.stTextInput > div > div > input {text-align: center;}</style>", unsafe_allow_html=True)
    st.markdown("<br><br>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 1, 1])
    with col2:
        st.title("🔒 Login Dashboard")
        st.write("Silakan masukkan Username & PIN Anda.")
        
        with st.form("login_form"):
            user_input = st.text_input("Username")
            pin_input = st.text_input("PIN", type="password", placeholder="****")
            submitted = st.form_submit_button("MASUK", use_container_width=True)
            
            if submitted:
                if user_input and pin_input:
                    is_valid, data_or_msg = authenticate_user(user_input, pin_input)
                    if is_valid:
                        st.session_state['logged_in'] = True
                        st.session_state['user_name'] = user_input
                        st.session_state['user_role'] = data_or_msg.get('role', 'staff')
                        st.session_state['user_branches'] = data_or_msg.get('access_branches', [])
                        
                        st.success(f"Selamat Datang, {user_input}!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error(f"Login Gagal: {data_or_msg}")
                else:
                    st.warning("Mohon isi Username dan PIN.")

def logout():
    for key in ['logged_in', 'user_name', 'user_role', 'user_branches']:
        if key in st.session_state:
            del st.session_state[key]
    st.rerun()

# ==============================================================================
# 4. CORE DATA FUNCTIONS (RESTORED ORIGINAL LOGIC)
# ==============================================================================

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
                    "Jam": ot.hour, 
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
            
            order_type = order.get('order_type', 'N/A')
            
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
                        "Tipe Order": order_type, 
                        "Qty": qty,
                        "Harga Satuan": price,
                        "Total": qty * price
                    })
        except: continue
    return pd.DataFrame(analysis)

# ==============================================================================
# 5. EXCEL REPORT GENERATOR (FULL 6 SHEETS RESTORED)
# ==============================================================================
def create_esb_style_excel(df_trx, df_items, raw_data_filtered, branch_name, start_date, end_date): 
    """
    Export Lengkap dengan 6 Sheet (Summary, Payment, Category, Item, Hourly, Transaction Log).
    """
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # --- STYLING ---
        fmt_title = workbook.add_format({'bold': True, 'font_size': 14, 'align': 'left'})
        fmt_subtitle = workbook.add_format({'italic': True, 'font_size': 10, 'align': 'left', 'font_color': '#555555'})
        fmt_th = workbook.add_format({'bold': True, 'align': 'center', 'valign': 'vcenter', 'bg_color': '#2C3E50', 'font_color': '#FFFFFF', 'border': 1})
        fmt_text = workbook.add_format({'border': 1, 'align': 'left', 'valign': 'top'})
        fmt_center = workbook.add_format({'border': 1, 'align': 'center', 'valign': 'top'})
        fmt_curr = workbook.add_format({'border': 1, 'num_format': 'Rp #,##0', 'align': 'right', 'valign': 'top'})
        fmt_empty_border = workbook.add_format({'border': 1, 'bg_color': '#FFFFFF'})
        fmt_total_label = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#ECF0F1', 'align': 'right'})
        fmt_total_val = workbook.add_format({'bold': True, 'border': 1, 'bg_color': '#ECF0F1', 'num_format': 'Rp #,##0', 'align': 'right'})

        # --- SHEET 1: SALES SUMMARY ---
        ws = workbook.add_worksheet('Sales Summary')
        ws.set_column('A:A', 30); ws.set_column('B:B', 20)
        
        ws.write('A1', f"SALES SUMMARY REPORT - {branch_name}", fmt_title)
        ws.write('A2', f"Periode: {start_date} s/d {end_date}", fmt_subtitle)
        
        if not df_trx.empty:
            total_bill = len(df_trx)
            gross_sales = df_trx['Subtotal'].sum()
            total_disc = df_trx['Diskon'].sum()
            total_svc = df_trx['Service'].sum()
            total_tax = df_trx['Tax'].sum()
            net_sales = df_trx['Grand Total'].sum()
            avg_bill = net_sales / total_bill if total_bill > 0 else 0

            row = 4
            ws.write(row, 0, "METRICS", fmt_th); ws.write(row, 1, "AMOUNT", fmt_th)
            row += 1
            kpis = [
                ("Gross Sales (Subtotal)", gross_sales),
                ("(-) Total Discount", total_disc),
                ("(+) Service Charge", total_svc),
                ("(+) Tax (PB1)", total_tax),
                ("(=) NET SALES", net_sales),
            ]
            for k, v in kpis:
                ws.write(row, 0, k, fmt_text); ws.write(row, 1, v, fmt_curr); row += 1
            row += 1
            ws.write(row, 0, "Total Transactions", fmt_text); ws.write(row, 1, total_bill, fmt_center); row += 1
            ws.write(row, 0, "Average per Bill", fmt_text); ws.write(row, 1, avg_bill, fmt_curr)

        # --- SHEET 2: PAYMENT REPORT ---
        ws_pay = workbook.add_worksheet('Payment Report')
        ws_pay.set_column('A:A', 25); ws_pay.set_column('B:B', 20); ws_pay.set_column('C:C', 15)
        ws_pay.write('A1', "PAYMENT METHOD REPORT", fmt_title)
        
        if not df_trx.empty:
            ws_pay.write('A3', "Payment Method", fmt_th); ws_pay.write('B3', "Total Amount", fmt_th); ws_pay.write('C3', "Trans. Count", fmt_th)
            pay_sum = df_trx.groupby('Metode Bayar').agg({'Grand Total': 'sum', 'Kode Unik': 'count'}).reset_index()
            r = 3
            for idx, row_data in pay_sum.iterrows():
                ws_pay.write(r, 0, row_data['Metode Bayar'], fmt_text)
                ws_pay.write(r, 1, row_data['Grand Total'], fmt_curr)
                ws_pay.write(r, 2, row_data['Kode Unik'], fmt_center)
                r += 1
            ws_pay.write(r, 0, "TOTAL", fmt_total_label)
            ws_pay.write(r, 1, pay_sum['Grand Total'].sum(), fmt_total_val)
            ws_pay.write(r, 2, pay_sum['Kode Unik'].sum(), fmt_total_val)

        # --- SHEET 3: CATEGORY SALES ---
        if not df_items.empty:
            ws_cat = workbook.add_worksheet('Category Sales')
            ws_cat.set_column('A:A', 25); ws_cat.set_column('B:B', 20); ws_cat.set_column('C:C', 15)
            ws_cat.write('A1', "SALES BY CATEGORY", fmt_title)
            cat_sum = df_items.groupby('Kategori').agg({'Total': 'sum', 'Qty': 'sum'}).reset_index().sort_values('Total', ascending=False)
            ws_cat.write('A3', "Category Name", fmt_th); ws_cat.write('B3', "Total Sales", fmt_th); ws_cat.write('C3', "Total Qty", fmt_th)
            r = 3
            for idx, row_data in cat_sum.iterrows():
                ws_cat.write(r, 0, row_data['Kategori'], fmt_text)
                ws_cat.write(r, 1, row_data['Total'], fmt_curr)
                ws_cat.write(r, 2, row_data['Qty'], fmt_center)
                r += 1
            ws_cat.write(r, 0, "TOTAL", fmt_total_label)
            ws_cat.write(r, 1, cat_sum['Total'].sum(), fmt_total_val)
            ws_cat.write(r, 2, cat_sum['Qty'].sum(), fmt_total_val)

        # --- SHEET 4: ITEM SALES (DETAIL) ---
        if not df_items.empty:
            ws_item = workbook.add_worksheet('Item Sales')
            ws_item.set_column('A:A', 20); ws_item.set_column('B:B', 30); ws_item.set_column('C:C', 20); ws_item.set_column('D:D', 10); ws_item.set_column('E:E', 20)
            ws_item.write('A1', "PRODUCT MIX REPORT (ITEM SALES)", fmt_title)
            item_sum = df_items.groupby(['Kategori', 'Nama Menu', 'Tipe Order']).agg({'Qty': 'sum', 'Total': 'sum'}).reset_index().sort_values(['Kategori', 'Total'], ascending=[True, False])
            headers = ["Category", "Item Name", "Order Type", "Qty Sold", "Total Sales"]
            for col_num, h in enumerate(headers): ws_item.write(2, col_num, h, fmt_th)
            r = 3
            for idx, row_data in item_sum.iterrows():
                ws_item.write(r, 0, row_data['Kategori'], fmt_text)
                ws_item.write(r, 1, row_data['Nama Menu'], fmt_text)
                ws_item.write(r, 2, row_data['Tipe Order'], fmt_center)
                ws_item.write(r, 3, row_data['Qty'], fmt_center)
                ws_item.write(r, 4, row_data['Total'], fmt_curr)
                r += 1
            ws_item.write(r, 3, "TOTAL", fmt_total_label); ws_item.write(r, 4, item_sum['Total'].sum(), fmt_total_val)

        # --- SHEET 5: HOURLY SALES ---
        if not df_trx.empty:
            ws_hour = workbook.add_worksheet('Hourly Sales')
            ws_hour.set_column('A:A', 15); ws_hour.set_column('B:B', 20); ws_hour.set_column('C:C', 15)
            ws_hour.write('A1', "HOURLY SALES TREND", fmt_title)
            hour_sum = df_trx.groupby('Jam').agg({'Grand Total': 'sum', 'Kode Unik': 'count'}).reset_index().sort_values('Jam')
            ws_hour.write('A3', "Hour", fmt_th); ws_hour.write('B3', "Total Sales", fmt_th); ws_hour.write('C3', "Trans. Count", fmt_th)
            r = 3
            for idx, row_data in hour_sum.iterrows():
                jam_str = f"{int(row_data['Jam']):02d}:00 - {int(row_data['Jam'])+1:02d}:00"
                ws_hour.write(r, 0, jam_str, fmt_center)
                ws_hour.write(r, 1, row_data['Grand Total'], fmt_curr)
                ws_hour.write(r, 2, row_data['Kode Unik'], fmt_center)
                r += 1
            chart = workbook.add_chart({'type': 'column'})
            chart.add_series({'name': 'Sales Amount', 'categories': ['Hourly Sales', 3, 0, r-1, 0], 'values': ['Hourly Sales', 3, 1, r-1, 1], 'fill': {'color': '#3498DB'}})
            ws_hour.insert_chart('E3', chart)

        # --- SHEET 6: TRANSACTION LOG (RAW DATA) ---
        ws_log = workbook.add_worksheet('Transaction Log')
        headers_log = ['Kode Unik', 'Tanggal', 'Waktu', 'Tipe Order', 'Meja', 'Kasir', 'Metode Bayar', 'Grand Total', 'Item Name', 'Qty', 'Item Price', 'Item Total']
        for i, h in enumerate(headers_log): ws_log.write(0, i, h, fmt_th)
        ws_log.set_column('A:A', 20); ws_log.set_column('B:C', 12); ws_log.set_column('D:D', 15); ws_log.set_column('G:G', 15); ws_log.set_column('H:H', 15); ws_log.set_column('I:I', 35)
        
        curr_row = 1
        if raw_data_filtered:
            for trx in raw_data_filtered:
                trx_id = trx.get('order_id', trx.get('unique_code', 'N/A'))
                ts = trx.get('timestamp') or trx.get('completed_time')
                ot = parse_flexible_date(ts)
                tgl_str = ot.strftime("%Y-%m-%d") if ot else "-"
                jam_str = ot.strftime("%H:%M:%S") if ot else "-"
                trx_type = trx.get('order_type', '-')
                table = trx.get('table_number', '-')
                cashier = trx.get('cashier', 'System')
                pay_method = trx.get('payment_method', '-')
                if isinstance(pay_method, list): pay_method = ", ".join(pay_method)
                grand_total = float(trx.get('total_final', trx.get('total', 0)))
                items = trx.get('items', [])
                if isinstance(items, dict): items = list(items.values())
                
                first_item_in_trx = True
                if not items:
                    ws_log.write(curr_row, 0, trx_id, fmt_text); ws_log.write(curr_row, 1, tgl_str, fmt_center)
                    ws_log.write(curr_row, 2, jam_str, fmt_center); ws_log.write(curr_row, 3, trx_type, fmt_center)
                    ws_log.write(curr_row, 4, table, fmt_center); ws_log.write(curr_row, 5, cashier, fmt_center)
                    ws_log.write(curr_row, 6, pay_method, fmt_text); ws_log.write(curr_row, 7, grand_total, fmt_curr)
                    ws_log.write(curr_row, 8, "NO ITEMS", fmt_text)
                    curr_row += 1
                else:
                    for item in items:
                        if first_item_in_trx:
                            ws_log.write(curr_row, 0, trx_id, fmt_text); ws_log.write(curr_row, 1, tgl_str, fmt_center)
                            ws_log.write(curr_row, 2, jam_str, fmt_center); ws_log.write(curr_row, 3, trx_type, fmt_center)
                            ws_log.write(curr_row, 4, table, fmt_center); ws_log.write(curr_row, 5, cashier, fmt_center)
                            ws_log.write(curr_row, 6, pay_method, fmt_text); ws_log.write(curr_row, 7, grand_total, fmt_curr)
                            first_item_in_trx = False
                        else:
                            for c in range(8): ws_log.write(curr_row, c, "", fmt_empty_border)
                        
                        i_name = item.get('name', 'Unknown')
                        i_qty = float(item.get('quantity', item.get('qty', 1)))
                        i_price = float(item.get('price', 0))
                        i_total = i_qty * i_price
                        ws_log.write(curr_row, 8, i_name, fmt_text); ws_log.write(curr_row, 9, i_qty, fmt_center)
                        ws_log.write(curr_row, 10, i_price, fmt_curr); ws_log.write(curr_row, 11, i_total, fmt_curr)
                        curr_row += 1
    return output

def create_promo_cancel_excel(branch_name, start_date, end_date, raw_data):
    """
    Generate Excel untuk Laporan Promo dan Laporan Cancel/Void.
    """
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # --- Common Formats ---
        fmt_header_doc = workbook.add_format({'bold': True, 'font_size': 12})
        fmt_table_header = workbook.add_format({'bold': True, 'bg_color': '#CCCCCC', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
        fmt_text = workbook.add_format({'border': 1, 'align': 'left', 'valign': 'top'})
        fmt_center = workbook.add_format({'border': 1, 'align': 'center', 'valign': 'top'})
        fmt_date_val = workbook.add_format({'border': 1, 'align': 'left', 'valign': 'top', 'num_format': 'yyyy-mm-dd'})
        fmt_number = workbook.add_format({'border': 1, 'num_format': '#,##0.00', 'align': 'right', 'valign': 'top'})
        fmt_title = workbook.add_format({'bold': True, 'font_size': 14})
        
        # ======================================================================
        # SHEET 1: PROMOTION REPORT
        # ======================================================================
        ws_promo = workbook.add_worksheet('Promotion Report')
        
        # --- HEADER SECTION ---
        ws_promo.write('A1', "Promotion Report", fmt_title)
        ws_promo.write('A2', "PT Hoki Berkat Jaya", fmt_header_doc)
        ws_promo.write('A4', f"Generated: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
        ws_promo.write('A5', f"Period: {start_date} - {end_date}")
        ws_promo.write('A6', f"Branch: {branch_name}")
        ws_promo.write('A7', "Promotion Filter: Detail Bill")
        ws_promo.write('A8', "Company: PT Hoki Berkat Jaya")
        
        # --- TABLE HEADERS ---
        headers_promo = [
            "Branch", "Sales Date", "Promotion Type", "Promotion Name", "Sales Number",
            "Original Price", "Special Price", "Member Code", "Member Name",
            "External Member Code", "External Member Name", "External Member Type",
            "Employee Code", "Employee Name", "External Employee Code", "External Employee Name",
            "Main Menu Name", "Main Menu Code", "Menu Name", "Menu Code", "Qty",
            "Discount Total", "Voucher Discount", "Bill Total"
        ]
        
        for col, h in enumerate(headers_promo):
            ws_promo.write(9, col, h, fmt_table_header)
        
        # Set Widths
        ws_promo.set_column('A:A', 25); ws_promo.set_column('B:B', 12); ws_promo.set_column('C:E', 20)
        ws_promo.set_column('H:I', 15); ws_promo.set_column('V:X', 15)
        
        # --- DATA ROWS ---
        row_idx = 10
        if raw_data:
            for trx in raw_data:
                # Basic Trx Info
                ts = trx.get('timestamp') or trx.get('completed_time')
                ot = parse_flexible_date(ts)
                sales_date = ot.date() if ot else None
                sales_no = trx.get('order_id', trx.get('unique_code', '-'))
                
                # Financials
                subtotal = float(trx.get('subtotal', 0))
                bill_total = float(trx.get('total_final', 0))
                disc_amt = float(trx.get('discount_amount', 0))
                voucher_amt = float(trx.get('voucher_amount', 0))
                
                # Member & Employee Info
                member = trx.get('member', {}) if isinstance(trx.get('member'), dict) else {}
                mem_code = member.get('code', 'Non Member')
                mem_name = member.get('name', 'Non Member')
                
                cashier_name = trx.get('cashier', '-')
                
                # --- LOGIC: BILL LEVEL DISCOUNT ---
                if disc_amt > 0:
                    promo_name = trx.get('discount_name', 'General Discount')
                    promo_type = "DISCOUNT (%)" if ("%" in promo_name or "percent" in promo_name.lower()) else "DISCOUNT (AMT)"
                    
                    ws_promo.write(row_idx, 0, branch_name, fmt_text)
                    ws_promo.write(row_idx, 1, sales_date, fmt_date_val)
                    ws_promo.write(row_idx, 2, promo_type, fmt_text)
                    ws_promo.write(row_idx, 3, f"{promo_name} (BILL DISCOUNT)", fmt_text)
                    ws_promo.write(row_idx, 4, sales_no, fmt_text)
                    ws_promo.write(row_idx, 5, subtotal, fmt_number)
                    ws_promo.write(row_idx, 6, subtotal - disc_amt, fmt_number)
                    ws_promo.write(row_idx, 7, mem_code, fmt_text)
                    ws_promo.write(row_idx, 8, mem_name, fmt_text)
                    
                    # External Member/Employee Placeholders
                    ws_promo.write(row_idx, 9, "Non Member", fmt_text)
                    ws_promo.write(row_idx, 10, "Non Member", fmt_text)
                    ws_promo.write(row_idx, 11, "-", fmt_text)
                    ws_promo.write(row_idx, 12, "-", fmt_text)
                    ws_promo.write(row_idx, 13, cashier_name, fmt_text)
                    ws_promo.write(row_idx, 14, "-", fmt_text)
                    ws_promo.write(row_idx, 15, "-", fmt_text)
                    
                    # Menu Info (Empty for Bill Disc)
                    ws_promo.write(row_idx, 16, "-", fmt_text)
                    ws_promo.write(row_idx, 17, "-", fmt_text)
                    ws_promo.write(row_idx, 18, "-", fmt_text)
                    ws_promo.write(row_idx, 19, "-", fmt_text)
                    
                    ws_promo.write(row_idx, 20, 1.0, fmt_number) # Qty 1 for Bill Disc
                    ws_promo.write(row_idx, 21, disc_amt, fmt_number)
                    ws_promo.write(row_idx, 22, 0.0, fmt_number)
                    ws_promo.write(row_idx, 23, bill_total, fmt_number)
                    row_idx += 1
                
                # --- LOGIC: VOUCHER ---
                if voucher_amt > 0:
                     # Similar logic for voucher row if needed
                     pass

        # ======================================================================
        # SHEET 2: CANCEL MENU DETAIL REPORT
        # ======================================================================
        ws_cancel = workbook.add_worksheet('Cancel Menu Detail Report')
        
        # --- HEADER SECTION ---
        ws_cancel.write('A1', "Cancel Menu Detail Report", fmt_title)
        ws_cancel.write('A2', "PT Hoki Berkat Jaya", fmt_header_doc)
        ws_cancel.write('A4', f"Generated: {datetime.now().strftime('%d-%m-%Y %H:%M:%S')}")
        ws_cancel.write('A5', f"Period: {start_date} - {end_date}")
        ws_cancel.write('A6', f"Branch: {branch_name}")
        ws_cancel.write('A7', "Type: Cancel / Void (Default)")
        ws_cancel.write('A8', "Status: all")
        
        # --- TABLE HEADERS ---
        headers_cancel = [
            "Sales Number", "Branch", "Menu", "Menu Code", "Menu Category", 
            "Menu Category Detail", "Order By", "Order Time", "Cancel / Void By",
            "Cancel / Void Time", "Cancel / Void", "Cancel Notes", "Qty", 
            "Subtotal", "Service Charge", "Tax", "Total"
        ]
        
        for col, h in enumerate(headers_cancel):
            ws_cancel.write(9, col, h, fmt_table_header)
            
        ws_cancel.set_column('A:B', 20); ws_cancel.set_column('C:C', 25); ws_cancel.set_column('H:J', 18)
        
        # --- DATA ROWS ---
        row_c = 10
        if raw_data:
            for trx in raw_data:
                # Identify Voids: Check for 'void_items' key or item status
                voids_found = []
                
                # Method 1: void_items list in trx
                if 'void_items' in trx and isinstance(trx['void_items'], list):
                    voids_found.extend(trx['void_items'])
                
                # Method 2: items with status 'void'
                if 'items' in trx and isinstance(trx['items'], list):
                    for itm in trx['items']:
                        if isinstance(itm, dict) and (itm.get('status') == 'void' or itm.get('void_qty', 0) > 0):
                            voids_found.append(itm)
                
                # Method 3: Whole transaction void
                if trx.get('status') == 'void' or trx.get('order_status') == 'void':
                    # Treat all items as void
                    if 'items' in trx:
                        voids_found.extend(trx['items'])

                # Process found voids
                if voids_found:
                    sales_no = trx.get('order_id', trx.get('unique_code', '-'))
                    ts = trx.get('timestamp') or trx.get('completed_time')
                    order_time = parse_flexible_date(ts)
                    order_time_str = order_time.strftime("%Y-%m-%d %H:%M:%S") if order_time else "-"
                    
                    order_by = trx.get('cashier', 'System')
                    
                    for v_item in voids_found:
                        # Extract details
                        m_name = v_item.get('name', 'Unknown')
                        m_code = v_item.get('code', '')
                        m_cat = v_item.get('category', 'Food') # Default or extract
                        
                        # Void Details
                        v_by = v_item.get('void_by', trx.get('void_by', order_by))
                        v_time = v_item.get('void_time', order_time_str) # Fallback to order time if no specific void time
                        v_notes = v_item.get('void_reason', trx.get('void_reason', 'Cancelled'))
                        
                        qty = float(v_item.get('quantity', v_item.get('qty', 1)))
                        price = float(v_item.get('price', 0))
                        subtotal = qty * price
                        
                        # Tax/Service Calc (Estimate if not in item)
                        svc_rate = 0.05 # Assumption or fetch from config
                        tax_rate = 0.10
                        svc = subtotal * svc_rate
                        tax = (subtotal + svc) * tax_rate
                        total = subtotal + svc + tax

                        ws_cancel.write(row_c, 0, sales_no, fmt_text)
                        ws_cancel.write(row_c, 1, branch_name, fmt_text)
                        ws_cancel.write(row_c, 2, m_name, fmt_text)
                        ws_cancel.write(row_c, 3, m_code, fmt_text)
                        ws_cancel.write(row_c, 4, m_cat, fmt_text)
                        ws_cancel.write(row_c, 5, m_cat, fmt_text) # Detail same as cat
                        ws_cancel.write(row_c, 6, order_by, fmt_center)
                        ws_cancel.write(row_c, 7, order_time_str, fmt_center)
                        ws_cancel.write(row_c, 8, v_by, fmt_center)
                        ws_cancel.write(row_c, 9, v_time, fmt_center)
                        ws_cancel.write(row_c, 10, "Cancel", fmt_center)
                        ws_cancel.write(row_c, 11, v_notes, fmt_text)
                        ws_cancel.write(row_c, 12, qty, fmt_number)
                        ws_cancel.write(row_c, 13, subtotal, fmt_number)
                        ws_cancel.write(row_c, 14, svc, fmt_number)
                        ws_cancel.write(row_c, 15, tax, fmt_number)
                        ws_cancel.write(row_c, 16, total, fmt_number)
                        row_c += 1

    return output

# ==============================================================================
# 6. MAIN APP FLOW
# ==============================================================================

if not st.session_state['logged_in']:
    initialize_firebase() # Init once to allow Auto-Create Admin
    login_page()
else:
    # Sidebar
    with st.sidebar:
        st.title("⚙️ Pengaturan")
        st.info(f"User: **{st.session_state['user_name']}**\nRole: {st.session_state.get('user_role')}")
        if st.button("LOGOUT", use_container_width=True): logout()
        st.divider()
        debug_mode = st.checkbox("🔧 Mode Debug", value=False)
    
    st.title(f"📊 Dashboard Monitoring (Enterprise)")
    initialize_firebase()

    # --- LOGIKA HAK AKSES CABANG ---
    user_access = st.session_state.get('user_branches', [])
    if "ALL" in user_access:
        available_branches = ALL_BRANCHES_MASTER
    else:
        available_branches = [b for b in ALL_BRANCHES_MASTER if b in user_access]

    if not available_branches:
        st.error("Akun Anda tidak memiliki akses ke cabang manapun.")
        st.stop()

    selected_branch = st.selectbox("Pilih Cabang:", available_branches)

    if selected_branch:
        with st.spinner("Memuat data dari Cloud Firestore..."):
            history_data = fetch_data(selected_branch, debug_mode)
            current_menu_config = fetch_menu_config(selected_branch)

        # TAB DEFINITION
        # Tab Admin & Editor hanya muncul utk Owner/Manager
        user_role = st.session_state.get('user_role', 'staff')
        
        tab_list = ["📈 Ringkasan & KPI", "📄 Data Detail (Export)", "🍔 Lihat Menu (View)"]
        if user_role in ['administrator', 'manager']:
            tab_list.append("📝 Editor Menu (Admin)")
        if user_role == 'administrator':
            tab_list.append("👥 Manajemen User")
        
        tabs = st.tabs(tab_list)

        # PROSES DATA
        df_display = process_data_for_display(history_data)
        df_analysis = process_data_for_analysis(history_data, current_menu_config)
        
        # --- TAB 1: RINGKASAN & ANALISA ---
        with tabs[0]:
            st.subheader("📊 Analisa Bisnis")
            if not df_display.empty:
                min_date = df_display['Tanggal'].min(); max_date = df_display['Tanggal'].max()
            else:
                min_date = date.today(); max_date = date.today()
                
            c1, c2 = st.columns(2)
            d1 = c1.date_input("Dari Tanggal", min_date)
            d2 = c2.date_input("Sampai Tanggal", max_date)
            
            # --- FILTER LOGIC ---
            df_filtered = pd.DataFrame()
            df_filtered_analysis = pd.DataFrame()
            raw_data_filtered = []

            if not df_display.empty:
                mask_display = (df_display['Tanggal'] >= d1) & (df_display['Tanggal'] <= d2)
                df_filtered = df_display[mask_display]
                
                if not df_analysis.empty:
                    mask_analysis = (df_analysis['Tanggal'] >= d1) & (df_analysis['Tanggal'] <= d2)
                    df_filtered_analysis = df_analysis[mask_analysis]
                
                # Filter Raw List of Dicts (untuk Excel Export yang akurat)
                for trx in history_data:
                    ts = trx.get('timestamp') or trx.get('completed_time')
                    ot = parse_flexible_date(ts)
                    if ot:
                        trx_date = ot.date()
                        if d1 <= trx_date <= d2:
                            raw_data_filtered.append(trx)
                
                # KPI Cards
                tot = df_filtered['Grand Total'].sum()
                trx_count = len(df_filtered)
                avg_basket = tot / trx_count if trx_count > 0 else 0
                
                k1, k2, k3 = st.columns(3)
                k1.metric("Total Omset", f"Rp {tot:,.0f}")
                k2.metric("Total Transaksi", f"{trx_count} Bon")
                k3.metric("Rata-rata per Bon", f"Rp {avg_basket:,.0f}")
                
                st.divider()
                
                # Charts (Restored)
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

        # --- TAB 2: DETAIL & EXPORT (RESTORED FULL EXCEL) ---
        with tabs[1]:
            st.subheader("📄 Laporan Detail & Export")
            if df_display.empty: 
                st.info("Data kosong.")
            else:
                st.write("Data transaksi detail (Preview):")
                st.dataframe(df_display, use_container_width=True)
                
                st.divider()
                st.write("### 📥 Download Laporan")
                
                # Prepare data for export
                if not df_filtered.empty:
                    export_trx = df_filtered
                    export_items = df_filtered_analysis
                    export_raw = raw_data_filtered
                    f_start = str(d1); f_end = str(d2)
                else:
                    export_trx = df_display
                    export_items = df_analysis
                    export_raw = history_data
                    f_start = "ALL"; f_end = "ALL"
                
                col_d1, col_d2 = st.columns(2)
                
                with col_d1:
                    st.info("📊 **Laporan Standard (ESB Style)**\n\nSales Summary, Payment, Category, Item, Hourly, Transaction Log.")
                    if st.button("Download Excel (Standard)", key="btn_dl_std"):
                        filename = f"Laporan_Standard_{selected_branch}_{f_start}_sd_{f_end}.xlsx"
                        with st.spinner("Generating Standard Report..."):
                            excel_file = create_esb_style_excel(export_trx, export_items, export_raw, selected_branch, f_start, f_end)
                            st.download_button(
                                label="📥 Download Standard Report",
                                data=excel_file.getvalue(),
                                file_name=filename,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )
                            
                with col_d2:
                    st.success("🏷️ **Laporan Promo & Cancel**\n\nPromotion Report (Detail Bill) & Cancel Menu Detail Report.")
                    if st.button("Download Excel (Promo & Cancel)", key="btn_dl_promo"):
                        filename_p = f"Laporan_PromoCancel_{selected_branch}_{f_start}_sd_{f_end}.xlsx"
                        with st.spinner("Generating Promo & Cancel Report..."):
                            # Using raw_data_filtered ensures we respect the date range selected
                            excel_promo = create_promo_cancel_excel(selected_branch, f_start, f_end, export_raw)
                            st.download_button(
                                label="📥 Download Promo & Cancel Report",
                                data=excel_promo.getvalue(),
                                file_name=filename_p,
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                            )

        # --- TAB 3: LIHAT MENU ---
        with tabs[2]:
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

        # --- TAB 4: EDITOR MENU (Conditional for Owner/Manager) ---
        if user_role in ['administrator', 'manager'] and "📝 Editor Menu (Admin)" in tab_list:
            # Cari index dari list
            idx_edit = tab_list.index("📝 Editor Menu (Admin)")
            with tabs[idx_edit]:
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

        # --- TAB 5: USER MANAGEMENT (Owner Only) ---
        if user_role == 'administrator' and "👥 Manajemen User" in tab_list:
            idx_user = tab_list.index("👥 Manajemen User")
            with tabs[idx_user]:
                st.subheader("Manajemen Hak Akses User")
                col_add, col_list = st.columns([1, 2])
                
                with col_add:
                    st.write("#### Tambah User Baru")
                    with st.form("add_user_form"):
                        new_u = st.text_input("Username Baru (tanpa spasi)")
                        new_p = st.text_input("PIN (Password)")
                        new_r = st.selectbox("Role", ["administrator", "manager", "staff"])
                        opts = ["ALL"] + ALL_BRANCHES_MASTER
                        new_b = st.multiselect("Akses Cabang", opts, default=["Testing"])
                        
                        add_sub = st.form_submit_button("Buat User")
                        if add_sub:
                            if new_u and new_p and new_b:
                                scs, msg = add_new_user_to_db(new_u, new_p, new_r, new_b)
                                if scs: st.success(msg); time.sleep(1); st.rerun()
                                else: st.error(msg)
                            else:
                                st.warning("Lengkapi semua data.")
                
                with col_list:
                    st.write("#### Daftar User Aktif")
                    users = get_all_users()
                    if users:
                        clean_users = []
                        for u in users:
                            clean_users.append({
                                "Username": u['username'],
                                "Role": u.get('role'),
                                "PIN": "****", 
                                "Akses Cabang": ", ".join(u.get('access_branches', []))
                            })
                        st.dataframe(pd.DataFrame(clean_users), use_container_width=True)
                        
                        st.write("#### Hapus User")
                        del_user = st.selectbox("Pilih User untuk dihapus", [u['username'] for u in users if u['username'] != 'admin'])
                        if st.button(f"Hapus User {del_user}", type="primary"):
                            if delete_user_from_db(del_user):
                                st.success(f"User {del_user} dihapus."); time.sleep(1); st.rerun()
                            else:
                                st.error("Gagal menghapus.")
                    else:
                        st.info("Belum ada user lain.")
