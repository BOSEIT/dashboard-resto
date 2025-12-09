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

st.set_page_config(layout="wide", page_title="Dashboard X-POS (Firestore)")

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
        # 1. Coba ambil dari config khusus
        config_ref = db.collection('branches').document(branch_name).collection('configuration').document('menu')
        doc = config_ref.get()
        if doc.exists:
            data = doc.to_dict()
            return data.get('items', {})
        
        # 2. Fallback ke last daily report
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
    processed = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            grand_total = float(order.get('total_final', order.get('total', 0)))
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
                    "Tipe Order": order.get('order_type', 'N/A'),
                    "Meja": order.get('table_number', 'N/A'),
                    "Grand Total": grand_total,
                    "Metode Bayar": pay_method,
                    "Detail Item": "; ".join([f"{i.get('quantity', i.get('qty', 1))}x {i.get('name')}" for i in items]),
                })
        except: continue
    return pd.DataFrame(processed)

def process_data_for_analysis(history_data, menu_data):
    cat_map = {}
    if isinstance(menu_data, dict):
        for c, items in menu_data.items():
            # Handle Dict structure (e.g., "CALAMARI": {...})
            if isinstance(items, dict):
                 for k in items:
                     cat_map[k] = c
            # Handle List structure (e.g., [{name: "CALAMARI", ...}])
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
                        "Total": qty * price
                    })
        except: continue
    return pd.DataFrame(analysis)

def create_excel_report(df_display, df_analysis): 
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        if not df_display.empty:
            df_out = df_display.copy()
            df_out['Tanggal'] = df_out['Tanggal'].astype(str)
            df_out['Waktu'] = df_out['Waktu'].astype(str)
            df_out.to_excel(writer, index=False, sheet_name='Data Transaksi')

        if not df_analysis.empty:
            df_an = df_analysis.copy()
            df_an['Tanggal'] = df_an['Tanggal'].astype(str)
            df_an.to_excel(writer, index=False, sheet_name='Rincian Item')
            
            pivot = df_analysis.groupby('Nama Menu')['Qty'].sum().reset_index().sort_values('Qty', ascending=False)
            pivot.to_excel(writer, index=False, sheet_name='Top Menu')
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
    
    st.title(f"📊 Dashboard Monitoring (Cloud)")
    initialize_firebase()

    branches = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]
    selected_branch = st.selectbox("Pilih Cabang:", branches)

    if selected_branch:
        with st.spinner("Memuat data dari Cloud Firestore..."):
            history_data = fetch_data(selected_branch, debug_mode)
            current_menu_config = fetch_menu_config(selected_branch)

        tab1, tab2, tab_menu_view, tab_menu_edit = st.tabs([
            "📈 Ringkasan & KPI", "📄 Data Detail", "🍔 Lihat Menu (View)", "📝 Editor Menu (Admin)"
        ])

        df_display = process_data_for_display(history_data)
        df_analysis = process_data_for_analysis(history_data, current_menu_config)
        
        # --- TAB 1: RINGKASAN ---
        with tab1:
            st.subheader("Ringkasan Penjualan")
            if not df_display.empty:
                min_date = df_display['Tanggal'].min(); max_date = df_display['Tanggal'].max()
                c1, c2 = st.columns(2)
                d1 = c1.date_input("Dari Tanggal", min_date); d2 = c2.date_input("Sampai Tanggal", max_date)
                
                mask = (df_display['Tanggal'] >= d1) & (df_display['Tanggal'] <= d2)
                df_filtered = df_display[mask]
                
                tot = df_filtered['Grand Total'].sum()
                st.metric("Total Omset (Periode Ini)", f"Rp {tot:,.0f}")
                
                daily_chart = df_filtered.groupby('Tanggal')['Grand Total'].sum().reset_index()
                st.altair_chart(alt.Chart(daily_chart).mark_bar().encode(x='Tanggal', y='Grand Total', tooltip=['Tanggal', 'Grand Total']).interactive(), use_container_width=True)
            else:
                st.info("Belum ada data transaksi.")

        # --- TAB 2: DETAIL ---
        with tab2:
            if df_display.empty: st.info("Data kosong.")
            else:
                st.dataframe(df_display, use_container_width=True)
                if st.button("Download Excel Laporan"):
                    excel = create_excel_report(df_display, df_analysis)
                    st.download_button("Klik Download", excel.getvalue(), f"Laporan_{selected_branch}.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        # --- TAB 3: LIHAT MENU (READ ONLY) ---
        with tab_menu_view:
            st.subheader(f"Daftar Menu Aktif - {selected_branch}")
            view_data = []
            
            if current_menu_config:
                # Loop semua Kategori (e.g., "APPETIZER (FOOD)")
                for category, items in current_menu_config.items():
                    # Jika struktur item adalah Dictionary { "NamaItem": { detail } }
                    if isinstance(items, dict):
                        for k, v in items.items():
                            view_data.append({
                                "Kategori": category,
                                "Nama Menu": k,
                                "Harga": float(v.get('price', 0)),
                                "Harga Online": float(v.get('online_price', 0)),
                                "Printer": v.get('printer', 'KITCHEN')
                            })
                    # Jika struktur item adalah List [{ "name": "...", "price": ... }]
                    elif isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                view_data.append({
                                    "Kategori": category,
                                    "Nama Menu": item.get('name', ''),
                                    "Harga": float(item.get('price', 0)),
                                    "Harga Online": float(item.get('online_price', 0)),
                                    "Printer": item.get('printer', 'KITCHEN')
                                })
            
            if view_data:
                df_view = pd.DataFrame(view_data).sort_values(by=["Kategori", "Nama Menu"])
                st.dataframe(
                    df_view,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Harga": st.column_config.NumberColumn(format="Rp %d"),
                        "Harga Online": st.column_config.NumberColumn(format="Rp %d"),
                    }
                )
            else:
                st.info("Data menu belum tersedia.")

        # --- TAB 4: EDITOR MENU (REVISI) ---
        with tab_menu_edit:
            st.subheader(f"🛠️ Editor Menu - {selected_branch}")
            st.info("Edit menu di bawah ini. 'Online Price' sudah ditambahkan.")
            
            # 1. Prepare Data for Editor (Flattening)
            edit_data = []
            known_categories = set() # Untuk menyimpan kategori yang sudah ada
            
            # Kategori default jika belum ada data sama sekali
            default_categories = ["FOOD", "BEVERAGE", "SNACK", "OTHERS", "PAKET", "APPETIZER (FOOD)", "MAIN COURSE (FOOD)"]

            if current_menu_config:
                for category, items in current_menu_config.items():
                    known_categories.add(category)
                    
                    # Logic membaca data yang sama seperti View
                    if isinstance(items, dict): # Format Dict of Dicts (Yang kamu pakai sekarang)
                        for k, v in items.items():
                             edit_data.append({
                                    "Kategori": category,
                                    "Nama Menu": k,
                                    "Harga": float(v.get('price', 0)),
                                    "Harga Online": float(v.get('online_price', 0)),
                                    "Printer": v.get('printer', 'KITCHEN')
                                })
                    elif isinstance(items, list): # Format List (Legacy/Cadangan)
                        for item in items:
                            if isinstance(item, dict):
                                edit_data.append({
                                    "Kategori": category,
                                    "Nama Menu": item.get('name', ''),
                                    "Harga": float(item.get('price', 0)),
                                    "Harga Online": float(item.get('online_price', 0)),
                                    "Printer": item.get('printer', 'KITCHEN')
                                })

            # Gabungkan kategori yang ditemukan + default
            all_cat_options = list(known_categories.union(set(default_categories)))
            all_cat_options.sort()

            # Placeholder row jika kosong
            if not edit_data:
                edit_data.append({
                    "Kategori": "APPETIZER (FOOD)", 
                    "Nama Menu": "CALAMARI", 
                    "Harga": 48000, 
                    "Harga Online": 57600, 
                    "Printer": "KITCHEN"
                })

            df_editor_source = pd.DataFrame(edit_data)

            # 2. Show Editor
            # Urutan kolom kita paksa rapi
            column_order = ["Kategori", "Nama Menu", "Harga", "Harga Online", "Printer"]
            
            edited_df = st.data_editor(
                df_editor_source,
                num_rows="dynamic",
                use_container_width=True,
                hide_index=True,
                column_order=column_order,
                column_config={
                    "Kategori": st.column_config.SelectboxColumn(
                        "Kategori",
                        width="medium",
                        options=all_cat_options, # Opsi dinamis dari data yg ada
                        required=True
                    ),
                    "Nama Menu": st.column_config.TextColumn(
                        "Nama Menu",
                        width="large",
                        required=True
                    ),
                    "Harga": st.column_config.NumberColumn(
                        "Harga (Rp)",
                        format="%d",
                        min_value=0,
                        step=500,
                        width="small",
                        required=True
                    ),
                    "Harga Online": st.column_config.NumberColumn(
                        "Harga Online (Rp)",
                        format="%d",
                        min_value=0,
                        step=500,
                        width="small",
                        required=True
                    ),
                    "Printer": st.column_config.SelectboxColumn(
                        "Target Printer",
                        width="medium",
                        options=["KITCHEN", "BAR", "CASHIER", "PASTRY"], # Disamakan uppercase biar rapi
                        required=True
                    )
                }
            )

            # 3. Save Button
            if st.button("💾 Simpan Perubahan ke Cloud", type="primary"):
                # Kita akan membangun ulang Dictionary of Dictionaries
                # Struktur: { "APPETIZER (FOOD)": { "CALAMARI": { "price": ..., "printer": ... } } }
                
                new_menu_dict = {}
                try:
                    for index, row in edited_df.iterrows():
                        cat = row['Kategori'].strip() if row['Kategori'] else "OTHERS"
                        name = str(row['Nama Menu']).strip()
                        price = float(row['Harga'])
                        online_price = float(row['Harga Online'])
                        printer = row['Printer']
                        
                        if not name: continue
                        
                        # Inisialisasi Kategori jika belum ada
                        if cat not in new_menu_dict: 
                            new_menu_dict[cat] = {} # Perhatikan: Ini DICT, bukan LIST
                        
                        # Masukkan Item sebagai Key di dalam Dict Kategori
                        new_menu_dict[cat][name] = {
                            "price": price,
                            "online_price": online_price,
                            "printer": printer
                        }
                    
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
