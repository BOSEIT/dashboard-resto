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
# 2. FIREBASE AUTH & USER MANAGEMENT
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
                # Fallback untuk development lokal jika ada file json
                possible_files = ['serviceAccountKey.json', 'firebase-credentials.json']
                cred_file = None
                for f in possible_files:
                    if os.path.exists(f):
                        cred_file = f
                        break
                
                if cred_file:
                    cred = credentials.Certificate(cred_file)
                    firebase_admin.initialize_app(cred)
    except Exception as e:
        st.error(f"Gagal inisialisasi Firebase: {e}")

initialize_firebase()
db = firestore.client()

def get_user_role(username):
    """Mendapatkan role dan akses cabang user."""
    try:
        if username == "admin": 
            return "admin", ALL_BRANCHES_MASTER
        
        doc = db.collection('dashboard_users').document(username).get()
        if doc.exists:
            data = doc.to_dict()
            return data.get('role', 'viewer'), data.get('access_branches', [])
        return None, []
    except:
        return None, []

def verify_user(username, pin):
    """Verifikasi login sederhana."""
    try:
        # Backdoor untuk admin default (sebaiknya diganti di production)
        if username == "admin" and pin == "1234":
            return True

        doc = db.collection('dashboard_users').document(username).get()
        if doc.exists:
            data = doc.to_dict()
            return data.get('pin') == pin
        return False
    except:
        return False

def create_user_in_db(username, pin, role, branches):
    """Membuat user baru di Firestore."""
    try:
        db.collection('dashboard_users').document(username).set({
            'username': username,
            'pin': pin,
            'role': role,
            'access_branches': branches,
            'created_at': firestore.SERVER_TIMESTAMP
        })
        return True
    except Exception as e:
        st.error(f"Error creating user: {e}")
        return False

def delete_user_from_db(username):
    """Menghapus user."""
    try:
        db.collection('dashboard_users').document(username).delete()
        return True
    except:
        return False

def get_all_users():
    """List semua user untuk admin."""
    try:
        users = []
        docs = db.collection('dashboard_users').stream()
        for doc in docs:
            users.append(doc.to_dict())
        return users
    except:
        return []

# ==============================================================================
# 3. FUNGSI DATA & LOAD
# ==============================================================================

@st.cache_data(ttl=300)
def load_data(start_date, end_date, selected_branches):
    """Mengambil data transaksi dari Firestore berdasarkan filter."""
    all_data = []
    
    # Convert dates to datetime for query
    start_dt = datetime.combine(start_date, dt_time.min)
    end_dt = datetime.combine(end_date, dt_time.max)

    try:
        # Kita perlu query per cabang karena collection seringkali dipisah atau field branch_id
        # Asumsi struktur: Collection 'transactions' -> documents
        # Jika struktur berbeda (misal per cabang ada collection sendiri), sesuaikan di sini.
        
        # Query General ke collection 'transactions'
        # Note: Indexing compound di Firestore mungkin diperlukan untuk query ini
        query = db.collection('transactions')\
            .where('created_at', '>=', start_dt)\
            .where('created_at', '<=', end_dt)
            
        docs = query.stream()
        
        for doc in docs:
            d = doc.to_dict()
            # Filter cabang manual (client side filter) jika query 'in' bermasalah/terbatas
            branch_id = d.get('branch_id') or d.get('branch_name', 'Unknown')
            
            # Normalisasi nama cabang (sesuaikan dengan format di dropdown)
            # Logika sederhana: check substring
            matched_branch = None
            for b in ALL_BRANCHES_MASTER:
                if b in str(branch_id): # Misal ID "HOKEE_PIK_001" match "HOKEE_PIK"
                    matched_branch = b
                    break
            
            if not matched_branch:
                matched_branch = str(branch_id) # Pakai as-is jika tidak match master

            if matched_branch in selected_branches:
                d['doc_id'] = doc.id
                d['clean_branch'] = matched_branch
                
                # Pastikan field numeric ada
                d['total_amount'] = float(d.get('total_amount', 0))
                d['payment_method'] = d.get('payment_method', 'Cash')
                d['status'] = d.get('status', 'completed')
                
                # Handle timestamp
                if 'created_at' in d:
                    # Firestore timestamp to python datetime
                    if hasattr(d['created_at'], 'date'): 
                        # Sudah datetime/timestamp object
                        pass 
                    else:
                        # String parsing fallback
                        pass 
                
                all_data.append(d)
                
    except Exception as e:
        st.error(f"Gagal mengambil data: {e}")
        return pd.DataFrame()

    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    
    # Konversi created_at ke datetime pandas
    if 'created_at' in df.columns:
        df['created_at'] = pd.to_datetime(df['created_at'], utc=True).dt.tz_convert('Asia/Jakarta')
        df['date_only'] = df['created_at'].dt.date
        df['hour'] = df['created_at'].dt.hour
    
    return df

# ==============================================================================
# 4. EXCEL REPORT GENERATORS (CUSTOM REQUEST)
# ==============================================================================

def generate_promotion_report_excel(df, start_date, end_date, company_name="PT Hoki Berkat Jaya"):
    """
    Generate Excel Promotion Report sesuai format gambar user.
    """
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("Promotion Report")

    # Format
    fmt_title = workbook.add_format({'bold': True, 'font_size': 14})
    fmt_bold = workbook.add_format({'bold': True})
    fmt_header = workbook.add_format({'bold': True, 'bg_color': '#f2f2f2', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
    fmt_date = workbook.add_format({'num_format': 'dd-mm-yyyy hh:mm:ss'})
    fmt_date_short = workbook.add_format({'num_format': 'yyyy-mm-dd'})
    fmt_currency = workbook.add_format({'num_format': '#,##0.00'})
    fmt_number = workbook.add_format({'num_format': '#,##0.00'})
    
    # --- HEADER SECTION ---
    current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    period_str = f"{start_date.strftime('%d-%m-%Y')} - {end_date.strftime('%d-%m-%Y')}"
    
    # Ambil nama cabang dari data (ambil unique pertama atau gabungan)
    branches = df['clean_branch'].unique() if not df.empty else ["All"]
    branch_str = ", ".join(branches) if len(branches) < 3 else f"{len(branches)} Branches"

    worksheet.write('A1', 'Promotion Report', fmt_title)
    worksheet.write('A2', company_name, fmt_bold)
    
    worksheet.write('A4', 'Generated')
    worksheet.write('B4', current_time)
    
    worksheet.write('A5', 'Period')
    worksheet.write('B5', period_str)
    
    worksheet.write('A6', 'Branch')
    worksheet.write('B6', branch_str)
    
    worksheet.write('A7', 'Promotion Filter')
    worksheet.write('B7', 'Detail Bill')
    
    worksheet.write('A8', 'Company')
    worksheet.write('B8', company_name)

    # --- TABLE COLUMNS ---
    headers = [
        "Branch", "Sales Date", "Promotion Type", "Promotion Name", "Sales Number", 
        "Original Price", "Special Price", "Member Code", "Member Name", 
        "External Member Code", "External Member Name", "External Member Type", 
        "Employee Code", "Employee Name", "External Employee Code", 
        "External Employee Name", "Main Menu Name", "Main Menu Code", 
        "Menu Name", "Menu Code", "Qty", "Discount Total", 
        "Voucher Discount", "Bill Total"
    ]
    
    start_row = 10
    for col_num, header in enumerate(headers):
        worksheet.write(start_row, col_num, header, fmt_header)
        worksheet.set_column(col_num, col_num, 15) # Default width

    # --- DATA PROCESSING ---
    row_idx = start_row + 1
    
    # Kita harus iterasi setiap transaksi dan mencari diskon
    # Asumsi: Data diskon ada di dalam field 'discounts' (list) atau 'items' (jika diskon per item)
    # Karena struktur DB asli tidak diketahui pasti, kita buat robust handler
    
    for _, row in df.iterrows():
        # Data Level Transaksi
        branch = row.get('clean_branch', '')
        sales_date = row.get('date_only', '')
        sales_no = row.get('bill_no', row.get('doc_id', ''))
        bill_total = row.get('total_amount', 0)
        
        # Member Info (Default dash if not exist)
        member = row.get('member', {})
        if isinstance(member, dict):
            mem_code = member.get('code', 'Non Member')
            mem_name = member.get('name', 'Non Member')
        else:
            mem_code = 'Non Member'
            mem_name = 'Non Member'
            
        # Logika Ekstraksi Diskon
        # 1. Cek Bill Discount
        discounts = row.get('discounts', [])
        if isinstance(discounts, list):
            for disc in discounts:
                # Menulis baris untuk Bill Discount
                promo_type = "DISCOUNT (%)" if '%' in str(disc.get('name', '')) else "DISCOUNT (AMT)"
                promo_name = disc.get('name', 'Unknown Discount')
                disc_total = float(disc.get('amount', 0))
                
                worksheet.write(row_idx, 0, branch)
                worksheet.write(row_idx, 1, sales_date, fmt_date_short)
                worksheet.write(row_idx, 2, promo_type)
                worksheet.write(row_idx, 3, promo_name)
                worksheet.write(row_idx, 4, sales_no)
                worksheet.write(row_idx, 5, "") # Original Price (biasanya N/A utk bill disc)
                worksheet.write(row_idx, 6, "") # Special Price
                worksheet.write(row_idx, 7, mem_code)
                worksheet.write(row_idx, 8, mem_name)
                # External info placeholders
                worksheet.write(row_idx, 9, "-")
                worksheet.write(row_idx, 10, "-")
                worksheet.write(row_idx, 11, "-")
                worksheet.write(row_idx, 12, "-") # Employee Code
                worksheet.write(row_idx, 13, "-") # Employee Name
                worksheet.write(row_idx, 14, "-")
                worksheet.write(row_idx, 15, "-")
                worksheet.write(row_idx, 16, "-") # Main Menu Name
                worksheet.write(row_idx, 17, "-")
                worksheet.write(row_idx, 18, "-") # Menu Name
                worksheet.write(row_idx, 19, "-")
                worksheet.write(row_idx, 20, 1, fmt_number) # Qty 1 untuk bill discount
                worksheet.write(row_idx, 21, disc_total, fmt_currency)
                worksheet.write(row_idx, 22, 0, fmt_currency) # Voucher
                worksheet.write(row_idx, 23, bill_total, fmt_currency)
                row_idx += 1

        # 2. Cek Item Discount (Jika ada di dalam list items)
        items = row.get('items', [])
        if isinstance(items, list):
            for item in items:
                item_disc = float(item.get('discount_amount', 0))
                if item_disc > 0:
                    worksheet.write(row_idx, 0, branch)
                    worksheet.write(row_idx, 1, sales_date, fmt_date_short)
                    worksheet.write(row_idx, 2, "ITEM DISCOUNT")
                    worksheet.write(row_idx, 3, f"Disc on {item.get('name')}")
                    worksheet.write(row_idx, 4, sales_no)
                    worksheet.write(row_idx, 5, float(item.get('price', 0)), fmt_currency)
                    worksheet.write(row_idx, 6, float(item.get('price', 0)) - item_disc, fmt_currency)
                    worksheet.write(row_idx, 7, mem_code)
                    worksheet.write(row_idx, 8, mem_name)
                    # ... (Fill others as dash)
                    for c in range(9, 16): worksheet.write(row_idx, c, "-")
                    
                    worksheet.write(row_idx, 16, item.get('category', '-'))
                    worksheet.write(row_idx, 17, "-")
                    worksheet.write(row_idx, 18, item.get('name', 'Unknown Item'))
                    worksheet.write(row_idx, 19, item.get('code', '-'))
                    worksheet.write(row_idx, 20, float(item.get('qty', 1)), fmt_number)
                    worksheet.write(row_idx, 21, item_disc, fmt_currency)
                    worksheet.write(row_idx, 22, 0, fmt_currency)
                    worksheet.write(row_idx, 23, bill_total, fmt_currency)
                    row_idx += 1

    workbook.close()
    return output.getvalue()

def generate_cancel_report_excel(df, start_date, end_date, company_name="PT Hoki Berkat Jaya"):
    """
    Generate Excel Cancel Menu Detail Report sesuai format gambar user.
    """
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    worksheet = workbook.add_worksheet("Cancel Report")

    # Format
    fmt_title = workbook.add_format({'bold': True, 'font_size': 14})
    fmt_bold = workbook.add_format({'bold': True})
    fmt_header = workbook.add_format({'bold': True, 'bg_color': '#f2f2f2', 'border': 1, 'align': 'center', 'valign': 'vcenter'})
    fmt_date_time = workbook.add_format({'num_format': 'yyyy-mm-dd hh:mm:ss'})
    fmt_currency = workbook.add_format({'num_format': '#,##0.00'})
    fmt_number = workbook.add_format({'num_format': '#,##0.00'})

    # --- HEADER SECTION ---
    current_time = datetime.now().strftime("%d-%m-%Y %H:%M:%S")
    period_str = f"{start_date.strftime('%d-%m-%Y')} - {end_date.strftime('%d-%m-%Y')}"
    branches = df['clean_branch'].unique() if not df.empty else ["All"]
    branch_str = ", ".join(branches) if len(branches) < 3 else f"{len(branches)} Branches"
    
    # Generate Nama File simulasi
    file_name_sim = f"Cancel Menu Detail Report - {datetime.now().strftime('%Y%m%d%H%M%S')}"

    worksheet.write('A1', 'Cancel Menu Detail Report', fmt_title)
    worksheet.write('A2', company_name, fmt_bold)
    
    worksheet.write('A4', 'Generated')
    worksheet.write('B4', current_time)
    worksheet.write('A5', 'Period')
    worksheet.write('B5', period_str)
    worksheet.write('A6', 'Branch')
    worksheet.write('B6', branch_str)
    worksheet.write('A7', 'Type')
    worksheet.write('B7', 'Cancel / Void (Default)')
    worksheet.write('A8', 'Status')
    worksheet.write('B8', 'all')
    worksheet.write('A9', 'Is Preview Bill')
    worksheet.write('B9', '1')
    worksheet.write('A10', 'Generated Username')
    worksheet.write('B10', st.session_state.get('username', 'Admin'))
    worksheet.write('A11', 'Report File Name')
    worksheet.write('B11', file_name_sim)

    # --- TABLE COLUMNS ---
    headers = [
        "Sales Number", "Branch", "Menu", "Menu Code", "Menu Category", 
        "Menu Category Detail", "Order By", "Order Time", "Cancel / Void By", 
        "Cancel / Void Time", "Cancel / Void", "Cancel Notes", "Qty", 
        "Subtotal", "Service Charge", "Tax", "Total"
    ]
    
    start_row = 13
    for col_num, header in enumerate(headers):
        worksheet.write(start_row, col_num, header, fmt_header)
        worksheet.set_column(col_num, col_num, 15)

    # --- DATA PROCESSING ---
    row_idx = start_row + 1
    
    # Iterasi data untuk mencari item yang di void/cancel
    for _, row in df.iterrows():
        branch = row.get('clean_branch', '')
        sales_no = row.get('bill_no', row.get('doc_id', ''))
        order_time = row.get('created_at', '') # Asumsi datetime object
        
        # Cek Items
        items = row.get('items', [])
        if isinstance(items, list):
            for item in items:
                # Logika: Jika status item adalah 'void' atau 'cancel' atau qty < 0 (tergantung implementasi POS)
                is_void = False
                status = item.get('status', '').lower()
                qty = float(item.get('qty', 0))
                
                if 'void' in status or 'cancel' in status:
                    is_void = True
                
                # Jika sistem menyimpan item void, biasanya qty positif tapi status void, 
                # atau dipisah di array 'void_items'. Kita asumsikan ada di items dgn status tertentu.
                
                if is_void:
                    menu_name = item.get('name', 'Unknown')
                    menu_code = item.get('code', '')
                    category = item.get('category', 'Food')
                    
                    price = float(item.get('price', 0))
                    subtotal = price * qty
                    # Hitung tax/svc (simulasi 5% svc, 10% tax, atau ambil dr data jika ada)
                    svc = subtotal * 0.05 
                    tax = (subtotal + svc) * 0.1
                    total = subtotal + svc + tax
                    
                    # Notes/Reason
                    notes = item.get('void_reason', item.get('note', '-'))
                    void_by = item.get('void_by', 'Admin')
                    void_time = item.get('void_at', order_time) # Fallback ke order time jika tdk ada
                    
                    if isinstance(void_time, str): pass # Handle string conversion if needed
                    elif hasattr(void_time, 'strftime'): void_time = void_time.strftime('%Y-%m-%d %H:%M:%S')

                    if hasattr(order_time, 'strftime'): 
                        order_time_str = order_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        order_time_str = str(order_time)

                    worksheet.write(row_idx, 0, sales_no)
                    worksheet.write(row_idx, 1, branch)
                    worksheet.write(row_idx, 2, menu_name)
                    worksheet.write(row_idx, 3, menu_code)
                    worksheet.write(row_idx, 4, category)
                    worksheet.write(row_idx, 5, category) # Category Detail (Placeholder)
                    worksheet.write(row_idx, 6, "SERVER") # Order By (Placeholder)
                    worksheet.write(row_idx, 7, order_time_str)
                    worksheet.write(row_idx, 8, void_by)
                    worksheet.write(row_idx, 9, str(void_time))
                    worksheet.write(row_idx, 10, "Cancel")
                    worksheet.write(row_idx, 11, notes)
                    worksheet.write(row_idx, 12, qty, fmt_number)
                    worksheet.write(row_idx, 13, subtotal, fmt_currency)
                    worksheet.write(row_idx, 14, svc, fmt_currency)
                    worksheet.write(row_idx, 15, tax, fmt_currency)
                    worksheet.write(row_idx, 16, total, fmt_currency)
                    row_idx += 1

    workbook.close()
    return output.getvalue()

# ==============================================================================
# 5. UI UTAMA (LOGIN & DASHBOARD)
# ==============================================================================

def login_page():
    st.markdown("<h1 style='text-align: center;'>🔐 X-POS Enterprise Login</h1>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("Username")
            pin = st.text_input("PIN", type="password")
            submitted = st.form_submit_button("Login", use_container_width=True)
            
            if submitted:
                if verify_user(username, pin):
                    role, branches = get_user_role(username)
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = username
                    st.session_state['role'] = role
                    st.session_state['access_branches'] = branches
                    st.success("Login Berhasil!")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Username atau PIN salah.")

def main_dashboard():
    # Sidebar Info
    st.sidebar.title(f"👤 {st.session_state['username']}")
    st.sidebar.caption(f"Role: {st.session_state['role'].upper()}")
    
    if st.sidebar.button("Logout"):
        st.session_state.clear()
        st.rerun()

    st.title("📊 Dashboard X-POS (Enterprise)")
    
    # --- FILTER SECTION ---
    with st.expander("🔍 Filter Data", expanded=True):
        col1, col2, col3 = st.columns(3)
        with col1:
            start_date = st.date_input("Dari Tanggal", date.today())
        with col2:
            end_date = st.date_input("Sampai Tanggal", date.today())
        with col3:
            # Filter cabang berdasarkan hak akses
            user_branches = st.session_state['access_branches']
            if "ALL" in user_branches or st.session_state['role'] == 'admin':
                available_branches = ALL_BRANCHES_MASTER
            else:
                available_branches = user_branches
            
            selected_branches = st.multiselect("Pilih Cabang", available_branches, default=available_branches)

    # --- LOAD DATA ---
    if st.button("Terapkan Filter", type="primary") or 'data_loaded' not in st.session_state:
        with st.spinner("Mengambil data..."):
            df = load_data(start_date, end_date, selected_branches)
            st.session_state['df_data'] = df
            st.session_state['data_loaded'] = True
    
    df = st.session_state.get('df_data', pd.DataFrame())

    if df.empty:
        st.warning("Tidak ada data ditemukan untuk periode ini.")
        # Tab Admin tetap harus bisa diakses meski data kosong
    else:
        # --- METRICS ---
        total_sales = df['total_amount'].sum()
        trx_count = len(df)
        avg_basket = total_sales / trx_count if trx_count > 0 else 0
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Total Penjualan", f"Rp {total_sales:,.0f}")
        m2.metric("Jumlah Transaksi", f"{trx_count}")
        m3.metric("Rata-rata Keranjang", f"Rp {avg_basket:,.0f}")

    # --- TABS NAVIGASI ---
    tab1, tab2, tab3 = st.tabs(["📈 Analisis Grafik", "📄 Laporan Detail", "⚙️ Admin Panel"])
    
    with tab1:
        if not df.empty:
            st.subheader("Tren Penjualan Harian")
            daily_sales = df.groupby('date_only')['total_amount'].sum().reset_index()
            chart_daily = alt.Chart(daily_sales).mark_line(point=True).encode(
                x='date_only', y='total_amount', tooltip=['date_only', 'total_amount']
            ).interactive()
            st.altair_chart(chart_daily, use_container_width=True)
            
            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Penjualan per Cabang")
                branch_sales = df.groupby('clean_branch')['total_amount'].sum().reset_index()
                chart_branch = alt.Chart(branch_sales).mark_bar().encode(
                    x='clean_branch', y='total_amount', color='clean_branch'
                )
                st.altair_chart(chart_branch, use_container_width=True)
            
            with col_b:
                st.subheader("Metode Pembayaran")
                pay_sales = df.groupby('payment_method')['total_amount'].sum().reset_index()
                chart_pay = alt.Chart(pay_sales).mark_arc().encode(
                    theta='total_amount', color='payment_method', tooltip=['payment_method', 'total_amount']
                )
                st.altair_chart(chart_pay, use_container_width=True)

    with tab2:
        if not df.empty:
            st.subheader("Data Transaksi Detail")
            st.dataframe(df, use_container_width=True)
            
            st.markdown("---")
            st.subheader("📥 Download Laporan Khusus")
            
            col_d1, col_d2 = st.columns(2)
            
            with col_d1:
                st.info("**Promotion Report**")
                st.markdown("Recap promo discount dengan detail member & item.")
                excel_promo = generate_promotion_report_excel(df, start_date, end_date)
                st.download_button(
                    label="Download Promotion Report (Excel)",
                    data=excel_promo,
                    file_name=f"Promotion_Report_{start_date}_{end_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
                
            with col_d2:
                st.info("**Cancel Menu Detail Report**")
                st.markdown("Laporan detail item yang di-cancel/void.")
                excel_cancel = generate_cancel_report_excel(df, start_date, end_date)
                st.download_button(
                    label="Download Cancel Report (Excel)",
                    data=excel_cancel,
                    file_name=f"Cancel_Report_{start_date}_{end_date}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

    with tab3:
        if st.session_state['role'] != 'admin':
            st.error("Akses Ditolak. Halaman ini khusus Admin.")
        else:
            st.subheader("Manajemen User Dashboard")
            
            col_form, col_list = st.columns([1, 2])
            
            with col_form:
                st.write("#### Tambah User Baru")
                new_user = st.text_input("Username Baru")
                new_pin = st.text_input("PIN Baru", type="password")
                new_role = st.selectbox("Role", ["viewer", "manager", "admin"])
                new_access = st.multiselect("Akses Cabang", ALL_BRANCHES_MASTER)
                
                if st.button("Buat User", type="primary"):
                    if new_user and new_pin and new_access:
                        if create_user_in_db(new_user, new_pin, new_role, new_access):
                            st.success(f"User {new_user} berhasil dibuat!"); time.sleep(1); st.rerun()
                        else: st.error("Gagal membuat user.")
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

# ==============================================================================
# 6. MAIN EXECUTION
# ==============================================================================

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False

if not st.session_state['logged_in']:
    login_page()
else:
    main_dashboard()
