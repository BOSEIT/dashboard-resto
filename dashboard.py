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

st.set_page_config(layout="wide", page_title="Dashboard X-POS Analyst")

# Validasi User
VALID_USERS = {
    "Admin": "123",
    "Jason": "0000",
    "Rina": "0102",
    "Hendri": "1234",
    "Sandy": "0908"
}

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
                    st.error("⚠️ FILE KUNCI TIDAK DITEMUKAN!")
                    st.stop()
    except Exception as e:
        st.error(f"Firebase Init Error: {e}"); st.stop()

def get_firestore_client():
    return firestore.client()

def fetch_data(branch_name, debug_mode=False):
    """
    Mengambil data transaksi dari Cloud Firestore.
    """
    if debug_mode:
        st.cache_data.clear()
        
    try:
        db = get_firestore_client()
        reports_ref = db.collection('branches').document(branch_name).collection('daily_reports')
        
        docs = reports_ref.stream()
        
        all_transactions = []
        found_dates = []
        debug_info = []

        for doc in docs:
            data = doc.to_dict()
            date_key = doc.id
            found_dates.append(date_key)
            
            raw_trx = data.get('transactions', [])
            
            # [PENTING] Filter hanya tipe 'payment_success'
            valid_orders = []
            if raw_trx and isinstance(raw_trx, list):
                for t in raw_trx:
                    if not t.get('timestamp'):
                         t['timestamp'] = f"{date_key} 12:00:00"
                    valid_orders.append(t)
            
            if valid_orders:
                all_transactions.extend(valid_orders)
                debug_info.append(f"📅 {date_key}: {len(valid_orders)} logs found")
            else:
                debug_info.append(f"📅 {date_key}: 0 logs (Empty)")

        if debug_mode:
            st.info(f"🔍 Path: branches/{branch_name}/daily_reports")
            with st.expander("Detail Dokumen Ditemukan"):
                for d in debug_info: st.write(d)
                if all_transactions:
                    st.write("--- CONTOH DATA RAW PERTAMA ---")
                    st.json(all_transactions[0])
            
        return all_transactions

    except Exception as e:
        if debug_mode: st.error(f"Fetch Error: {e}")
        return []

@st.cache_data(ttl=60)
def fetch_menu(branch_name):
    try:
        db = get_firestore_client()
        docs = db.collection('branches').document(branch_name).collection('daily_reports')\
                 .order_by('date', direction=firestore.Query.DESCENDING).limit(1).stream()
        
        for doc in docs:
            data = doc.to_dict()
            return data.get('master_data', {}).get('menu', {})
        return {}
    except: return {}

# ==============================================================================
# 3. DATA PROCESSING
# ==============================================================================

def parse_flexible_date(ts):
    if not ts: return None
    if hasattr(ts, 'date'): return ts
    if isinstance(ts, (int, float)):
        try: return datetime.fromtimestamp(ts if ts < 10000000000 else ts/1000)
        except: pass
    
    ts_str = str(ts)
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
        try:
            return datetime.strptime(ts_str.split('.')[0], fmt)
        except: continue
    return None

def process_data_for_display(history_data):
    processed = []
    
    for order in history_data:
        try:
            # [FILTER] Hanya ambil yang tipe 'payment_success'
            if order.get('type') != 'payment_success':
                continue

            # Ambil Items
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            subtotal = sum(float(i.get('price', 0)) * float(i.get('quantity', i.get('qty', 1))) for i in items)
            grand_total = float(order.get('total_final', order.get('total', 0)))
            
            ts = order.get('timestamp') or order.get('completed_time')
            ot = parse_flexible_date(ts)

            if ot:
                pay_method = order.get('payment_method', '-')
                # Ambil alasan diskon (nama promo)
                disc_reason = order.get('discount_reason', '-')
                if not disc_reason: disc_reason = "-"

                processed.append({
                    "Kode Unik": order.get('order_id', order.get('unique_code', 'N/A')),
                    "Tanggal": ot.date(),
                    "Jam": ot.hour, # Untuk analisa jam sibuk
                    "Waktu": ot.time(),
                    "Tipe Order": order.get('order_type', 'Dine In'),
                    "Meja": order.get('table_number', '-'),
                    "Kasir": order.get('cashier', order.get('user', 'Staff')),
                    "Grand Total": grand_total,
                    "Subtotal": subtotal,
                    "Metode Bayar": pay_method,
                    "Detail Item": ", ".join([f"{i.get('quantity', i.get('qty', 1))}x {i.get('name')}" for i in items]),
                    "Diskon": float(order.get('discount_amount', 0)),
                    "Nama Promo": disc_reason, # FIELD BARU
                    "Service": float(order.get('service_charge', 0)),
                    "Pajak": float(order.get('tax_pb1', 0)),
                })
        except Exception as e: 
            continue
            
    return pd.DataFrame(processed)

def process_item_analysis(history_data, menu_data):
    # Mapping Kategori
    cat_map = {}
    if isinstance(menu_data, dict):
        for c, items in menu_data.items():
            if isinstance(items, dict):
                for k in items: cat_map[k] = c
            elif isinstance(items, list): 
                 for m in items:
                     if isinstance(m, dict): cat_map[m.get('name')] = c

    analysis = []
    for order in history_data:
        try:
            if order.get('type') != 'payment_success': continue 

            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            ts = order.get('timestamp')
            ot = parse_flexible_date(ts)
            
            if ot:
                for i in items:
                    nm = i.get('name', 'N/A')
                    qty = float(i.get('quantity', i.get('qty', 1)))
                    price = float(i.get('price', 0))
                    
                    analysis.append({
                        "Tanggal": ot.date(),
                        "Nama Menu": nm,
                        "Kategori": cat_map.get(nm, 'Lain-lain'),
                        "Qty": qty,
                        "Total Omset": qty * price
                    })
        except: continue
    
    return pd.DataFrame(analysis)

# ==============================================================================
# 4. EXCEL EXPORT
# ==============================================================================
def create_excel_report(df_display, df_analysis): 
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if not df_display.empty:
            df_display.drop(columns=['Jam'], errors='ignore').astype(str).to_excel(writer, index=False, sheet_name='Transaksi')
            
            # Sheet Khusus Promo
            df_promo = df_display[df_display['Diskon'] > 0][['Tanggal', 'Kode Unik', 'Nama Promo', 'Diskon', 'Grand Total']]
            if not df_promo.empty:
                 df_promo.to_excel(writer, index=False, sheet_name='Laporan Promo')

        if not df_analysis.empty:
            df_analysis.astype(str).to_excel(writer, index=False, sheet_name='Analisa Item')
            
    return output

# ==============================================================================
# 5. MAIN APP
# ==============================================================================

if not st.session_state['logged_in']:
    login()
else:
    with st.sidebar:
        st.title("⚙️ Pengaturan")
        st.write(f"User: **{st.session_state['user_name']}**")
        if st.button("LOGOUT"): logout()
        st.divider()
        debug_mode = st.checkbox("🔧 Mode Debug (Cek Raw Data)")
    
    st.title(f"📊 Dashboard Analyst")
    initialize_firebase()

    branches = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]
    selected_branch = st.selectbox("Pilih Cabang:", branches)

    if selected_branch:
        with st.spinner("Menganalisa data..."):
            history_data = fetch_data(selected_branch, debug_mode)
            menu_data = fetch_menu(selected_branch)

        # PROSES DATA
        df_display = process_data_for_display(history_data)
        df_item_analysis = process_item_analysis(history_data, menu_data)
        
        if df_display.empty and history_data:
            st.warning("⚠️ Data ditemukan tapi tidak ada transaksi sukses (payment_success).")
            if debug_mode: st.json(history_data[:3])

        # FILTER TANGGAL
        if not df_display.empty:
            min_d, max_d = df_display['Tanggal'].min(), df_display['Tanggal'].max()
        else:
            min_d, max_d = date.today(), date.today()
            
        c1, c2 = st.columns(2)
        start_date = c1.date_input("Dari", min_d)
        end_date = c2.date_input("Sampai", max_d)
        
        # APPLY FILTER
        if not df_display.empty:
            df_filt = df_display[(df_display['Tanggal'] >= start_date) & (df_display['Tanggal'] <= end_date)]
            df_item_filt = df_item_analysis[(df_item_analysis['Tanggal'] >= start_date) & (df_item_analysis['Tanggal'] <= end_date)]
        else:
            df_filt = pd.DataFrame()
            df_item_filt = pd.DataFrame()

        # ==========================
        # DASHBOARD ANALYST LAYOUT
        # ==========================
        
        # --- METRIK UTAMA ---
        st.markdown("### 1. Performansi Bisnis")
        if not df_filt.empty:
            tot_omset = df_filt['Grand Total'].sum()
            tot_trx = len(df_filt)
            tot_diskon = df_filt['Diskon'].sum()
            # AOV (Average Order Value)
            aov = tot_omset / tot_trx if tot_trx > 0 else 0
            # Net Sales (Estimasi kasar tanpa Pajak & Service)
            net_sales = df_filt['Subtotal'].sum() - tot_diskon

            m1, m2, m3, m4, m5 = st.columns(5)
            m1.metric("Total Omset (Gross)", f"Rp {tot_omset:,.0f}")
            m2.metric("Net Sales (Est.)", f"Rp {net_sales:,.0f}", help="Subtotal - Diskon")
            m3.metric("Total Transaksi", tot_trx)
            m4.metric("Rata2 Bill (AOV)", f"Rp {aov:,.0f}")
            m5.metric("Total Diskon Keluar", f"Rp {tot_diskon:,.0f}", delta_color="inverse")
        else:
            st.info("Tidak ada data di periode ini.")

        st.divider()

        # --- TABS ANALISA ---
        tabs = st.tabs(["🕒 Waktu & Pembayaran", "🍔 Menu & Kategori", "🏷️ Promo & Diskon", "📄 Data Transaksi"])
        
        # TAB 1: WAKTU & PAYMENT
        with tabs[0]:
            c_left, c_right = st.columns(2)
            
            with c_left:
                st.subheader("Jam Sibuk (Peak Hours)")
                if not df_filt.empty:
                    # Group by Jam
                    hourly = df_filt.groupby('Jam')['Kode Unik'].count().reset_index().rename(columns={'Kode Unik':'Jumlah Transaksi'})
                    chart_hour = alt.Chart(hourly).mark_bar().encode(
                        x=alt.X('Jam:O', title='Jam (24h)'),
                        y=alt.Y('Jumlah Transaksi', title='Jml Transaksi'),
                        color=alt.value('#FFAA00'),
                        tooltip=['Jam', 'Jumlah Transaksi']
                    ).properties(height=300)
                    st.altair_chart(chart_hour, use_container_width=True)
                else: st.write("-")

            with c_right:
                st.subheader("Metode Pembayaran")
                if not df_filt.empty:
                    pay_dist = df_filt.groupby('Metode Bayar')['Grand Total'].sum().reset_index()
                    chart_pay = alt.Chart(pay_dist).mark_arc(innerRadius=50).encode(
                        theta=alt.Theta(field="Grand Total", type="quantitative"),
                        color=alt.Color(field="Metode Bayar", type="nominal"),
                        tooltip=['Metode Bayar', 'Grand Total']
                    ).properties(height=300)
                    st.altair_chart(chart_pay, use_container_width=True)
                    
                    # Tabel kecil di bawah pie chart
                    st.dataframe(pay_dist.set_index('Metode Bayar').style.format("Rp {:,.0f}"), use_container_width=True)
                else: st.write("-")

        # TAB 2: MENU & KATEGORI
        with tabs[1]:
            c_cat, c_menu = st.columns([1, 2])
            
            with c_cat:
                st.subheader("Kontribusi Kategori")
                if not df_item_filt.empty:
                    cat_sales = df_item_filt.groupby('Kategori')['Total Omset'].sum().reset_index()
                    chart_cat = alt.Chart(cat_sales).mark_arc().encode(
                        theta='Total Omset',
                        color='Kategori',
                        tooltip=['Kategori', 'Total Omset']
                    )
                    st.altair_chart(chart_cat, use_container_width=True)
                    st.dataframe(cat_sales.sort_values('Total Omset', ascending=False).style.format({'Total Omset': 'Rp {:,.0f}'}), hide_index=True)
            
            with c_menu:
                st.subheader("Top 10 Menu Terlaris (Qty)")
                if not df_item_filt.empty:
                    top_menu = df_item_filt.groupby('Nama Menu')['Qty'].sum().reset_index().sort_values('Qty', ascending=False).head(10)
                    chart_menu = alt.Chart(top_menu).mark_bar().encode(
                        x='Qty',
                        y=alt.Y('Nama Menu', sort='-x'),
                        color=alt.value('#00AAFF'),
                        tooltip=['Nama Menu', 'Qty']
                    )
                    st.altair_chart(chart_menu, use_container_width=True)

        # TAB 3: PROMO & DISKON (NEW!)
        with tabs[2]:
            st.subheader("Analisa Penggunaan Promo")
            
            if not df_filt.empty:
                # Filter hanya yang ada diskon
                df_promo = df_filt[df_filt['Diskon'] > 0].copy()
                
                if df_promo.empty:
                    st.info("Tidak ada transaksi dengan promo/diskon di periode ini.")
                else:
                    k1, k2 = st.columns(2)
                    with k1:
                        st.write("#### Distribusi Nama Promo")
                        # Hitung frekuensi promo
                        # Split jika ada koma (misal: "Opening, Member")
                        all_promos = []
                        for reason in df_promo['Nama Promo']:
                            if reason and reason != '-':
                                parts = [p.strip() for p in reason.split(',')]
                                all_promos.extend(parts)
                        
                        if all_promos:
                            promo_counts = pd.Series(all_promos).value_counts().reset_index()
                            promo_counts.columns = ['Nama Promo', 'Frekuensi']
                            
                            chart_p = alt.Chart(promo_counts).mark_bar().encode(
                                x='Frekuensi',
                                y=alt.Y('Nama Promo', sort='-x'),
                                color=alt.value('#FF4444'),
                                tooltip=['Nama Promo', 'Frekuensi']
                            )
                            st.altair_chart(chart_p, use_container_width=True)
                        else:
                            st.write("Tidak ada nama promo spesifik tercatat.")

                    with k2:
                         st.write("#### Rincian Bill dengan Promo")
                         st.dataframe(
                             df_promo[['Tanggal', 'Jam', 'Kode Unik', 'Nama Promo', 'Diskon', 'Grand Total']]
                             .sort_values('Diskon', ascending=False)
                             .style.format({'Diskon': 'Rp {:,.0f}', 'Grand Total': 'Rp {:,.0f}'}),
                             use_container_width=True
                         )
            else:
                st.write("-")

        # TAB 4: DATA RAW
        with tabs[3]:
            st.dataframe(df_filt, use_container_width=True)
            if st.button("Download Laporan Lengkap (Excel)"):
                excel = create_excel_report(df_filt, df_item_filt)
                st.download_button("Klik Disini Untuk Download", excel.getvalue(), f"Laporan_{selected_branch}.xlsx")
