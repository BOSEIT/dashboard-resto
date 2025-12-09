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
        reports_ref = db.collection('branches').document(branch_name).collection('daily_reports')
        
        # Ambil semua dokumen
        docs = reports_ref.stream()
        
        all_transactions = []
        found_dates = []

        for doc in docs:
            data = doc.to_dict()
            date_key = doc.id
            found_dates.append(date_key)
            
            trx_list = data.get('transactions', [])
            
            # --- DEBUG BLOCK (Hanya muncul jika mode debug aktif) ---
            if debug_mode and len(found_dates) == 1:
                st.write(f"🔍 DEBUG: Contoh Data Raw (Tanggal: {date_key})")
                st.json(trx_list[0] if trx_list else {"Info": "List transaksi kosong"})
            # --------------------------------------------------------

            if trx_list and isinstance(trx_list, list):
                all_transactions.extend(trx_list)
            
            # Fallback jika transaksi kosong tapi ada summary (Data Manual/Lama)
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
            
        return all_transactions

    except Exception as e:
        if debug_mode: st.error(f"Fetch Error: {e}")
        return []

# --- MENU MANAGEMENT FUNCTIONS (BARU) ---

def fetch_menu_config(branch_name):
    """
    Mengambil konfigurasi menu yang bisa diedit.
    Logic:
    1. Cek 'configuration/menu' (Menu yang sudah pernah diedit admin).
    2. Jika kosong, ambil backup dari upload terakhir kasir ('daily_reports').
    """
    db = get_firestore_client()
    try:
        # 1. Coba ambil dari config khusus
        config_ref = db.collection('branches').document(branch_name).collection('configuration').document('menu')
        doc = config_ref.get()
        if doc.exists:
            data = doc.to_dict()
            return data.get('items', {})
        
        # 2. Fallback ke last daily report (ambil menu eksisting dari kasir)
        docs = db.collection('branches').document(branch_name).collection('daily_reports')\
                 .order_by('date', direction=firestore.Query.DESCENDING).limit(1).stream()
        for d in docs:
            # Struktur data report: master_data -> menu
            return d.to_dict().get('master_data', {}).get('menu', {})
        
        return {} # Jika benar-benar kosong
    except Exception as e:
        st.error(f"Gagal ambil data menu: {e}")
        return {}

def save_menu_config_to_cloud(branch_name, new_menu_data):
    """
    Menyimpan konfigurasi menu yang diedit ke Firestore.
    Disimpan ke: branches/{branch}/configuration/menu
    """
    try:
        db = get_firestore_client()
        config_ref = db.collection('branches').document(branch_name).collection('configuration').document('menu')
        
        payload = {
            "last_updated": firestore.SERVER_TIMESTAMP,
            "updated_by": st.session_state.get('user_name', 'Admin'),
            "items": new_menu_data
        }
        
        config_ref.set(payload)
        return True, "Menu berhasil disimpan ke Cloud! Kasir perlu tekan 'Download Menu' untuk update."
    except Exception as e:
        return False, f"Gagal simpan: {e}"

# ==============================================================================
# 3. DATA PROCESSING
# ==============================================================================

def parse_flexible_date(ts):
    """Fungsi pembantu untuk membaca berbagai format tanggal."""
    if not ts:
        return None
    
    if hasattr(ts, 'date'): 
        return ts
    
    ts_str = str(ts)
    try:
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        except ValueError:
            try:
                d = datetime.strptime(ts_str, "%Y-%m-%d").date()
                return datetime.combine(d, dt_time(0,0,0))
            except:
                return None

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
                if ot.tzinfo is not None:
                    ot = ot.replace(tzinfo=None)

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
        except: 
            continue
            
    return pd.DataFrame(processed)

def process_data_for_analysis(history_data, menu_data):
    # Buat mapping kategori sederhana
    cat_map = {}
    if isinstance(menu_data, dict):
        for c, items in menu_data.items():
            if isinstance(items, list): 
                 for m_item in items:
                     if isinstance(m_item, dict):
                         nm = m_item.get('name')
                         if nm: cat_map[nm] = c
            elif isinstance(items, dict): # Handle legacy format dict
                 for k in items:
                     cat_map[k] = c

    analysis = []
    for order in history_data:
        try:
            items = order.get('items', [])
            if isinstance(items, dict): items = list(items.values())
            
            if not items: continue

            ts = order.get('timestamp') or order.get('completed_time')
            ot = parse_flexible_date(ts)
            
            if ot:
                if ot.tzinfo is not None:
                    ot = ot.replace(tzinfo=None)

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

# ==============================================================================
# 4. EXCEL GENERATOR
# ==============================================================================
def create_excel_report(df_display, df_analysis): 
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        
        # SHEET 1: Data Transaksi
        if not df_display.empty:
            df_out = df_display.copy()
            df_out['Tanggal'] = df_out['Tanggal'].astype(str)
            df_out['Waktu'] = df_out['Waktu'].astype(str)
            df_out.to_excel(writer, index=False, sheet_name='Data Transaksi')

        # SHEET 2: Analisa Menu
        if not df_analysis.empty:
            df_an = df_analysis.copy()
            df_an['Tanggal'] = df_an['Tanggal'].astype(str)
            df_an.to_excel(writer, index=False, sheet_name='Rincian Item')
            
            # Pivot Summary
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
        if st.button("LOGOUT", use_container_width=True):
            logout()
        
        st.divider()
        debug_mode = st.checkbox("🔧 Mode Debug", value=False, help="Centang untuk melihat raw data Firestore.")
    
    st.title(f"📊 Dashboard Monitoring (Cloud)")
    initialize_firebase()

    branches = ["COLEGA_PIK", "HOKEE_PIK", "HOKEE_KG", "Testing"]
    selected_branch = st.selectbox("Pilih Cabang:", branches)

    if selected_branch:
        # --- LOAD DATA SECTION ---
        with st.spinner("Memuat data dari Cloud Firestore..."):
            history_data = fetch_data(selected_branch, debug_mode)
            # Fetch menu config untuk Editor
            current_menu_config = fetch_menu_config(selected_branch)

        # --- TABS SECTION ---
        # Kita tambah tab ke-4: "Editor Menu (Admin)"
        tab1, tab2, tab_menu_view, tab_menu_edit = st.tabs([
            "📈 Ringkasan & KPI", 
            "📄 Data Detail", 
            "🍔 Lihat Menu (Read-Only)",
            "📝 Editor Menu (Admin)"
        ])

        # DATA PROCESSING
        df_display = process_data_for_display(history_data)
        df_analysis = process_data_for_analysis(history_data, current_menu_config)
        
        # --- TAB 1: RINGKASAN ---
        with tab1:
            st.subheader("Ringkasan Penjualan")
            
            # Filter Tanggal Sederhana (Opsional bisa diperluas)
            if not df_display.empty:
                min_date = df_display['Tanggal'].min()
                max_date = df_display['Tanggal'].max()
                
                c1, c2 = st.columns(2)
                d1 = c1.date_input("Dari Tanggal", min_date)
                d2 = c2.date_input("Sampai Tanggal", max_date)
                
                # Filter Dataframe
                mask = (df_display['Tanggal'] >= d1) & (df_display['Tanggal'] <= d2)
                df_filtered = df_display[mask]
                
                tot = df_filtered['Grand Total'].sum()
                trx = len(df_filtered)
                
                k1, k2 = st.columns(2)
                k1.metric("Total Omset (Periode Ini)", f"Rp {tot:,.0f}")
                k2.metric("Total Transaksi", trx)
                
                # Chart
                daily_chart = df_filtered.groupby('Tanggal')['Grand Total'].sum().reset_index()
                st.altair_chart(alt.Chart(daily_chart).mark_bar().encode(
                    x='Tanggal', y='Grand Total', tooltip=['Tanggal', 'Grand Total']
                ).interactive(), use_container_width=True)
            else:
                st.info("Belum ada data transaksi yang masuk.")

        # --- TAB 2: DATA DETAIL ---
        with tab2:
            if df_display.empty:
                st.info("Tidak ada data transaksi detail untuk ditampilkan.")
            else:
                st.dataframe(df_display, use_container_width=True)
                
                if st.button("Download Excel Laporan"):
                    excel = create_excel_report(df_display, df_analysis)
                    st.download_button(
                        label="Klik Disini Untuk Download",
                        data=excel.getvalue(),
                        file_name=f"Laporan_{selected_branch}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

        # --- TAB 3: MENU (READ ONLY - VIEW LAMA) ---
        with tab_menu_view:
            st.write(f"### Menu Terakhir ({selected_branch})")
            st.caption("Ini adalah view read-only. Gunakan tab 'Editor Menu' untuk mengubah.")
            
            if not current_menu_config:
                st.warning("Data Menu belum tersedia.")
            else:
                # Tampilkan JSON raw atau list sederhana
                st.json(current_menu_config, expanded=False)

        # --- TAB 4: EDITOR MENU (FITUR BARU) ---
        with tab_menu_edit:
            st.subheader(f"🛠️ Editor Menu - {selected_branch}")
            st.markdown("""
            **Cara Menggunakan:**
            1. Edit nama, harga, atau kategori langsung di tabel di bawah.
            2. Anda juga bisa **menambahkan baris baru** (klik baris kosong paling bawah).
            3. Jika sudah selesai, klik tombol **"💾 Simpan Perubahan ke Cloud"**.
            4. Di aplikasi Kasir, tekan tombol **"Download Menu"** agar perubahan terupdate.
            """)
            
            # 1. PREPARE DATA FOR EDITOR
            # Kita perlu meratakan (flatten) dictionary menu yang bersarang menjadi List of Dictionaries
            # agar bisa ditampilkan di st.data_editor
            
            flat_menu_data = []
            
            if current_menu_config:
                for category, items in current_menu_config.items():
                    # Handle jika items adalah List (Standard Baru)
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict):
                                flat_menu_data.append({
                                    "Kategori": category,
                                    "Nama Menu": item.get('name', ''),
                                    "Harga": float(item.get('price', 0)),
                                    "Printer": item.get('printer', 'kitchen') # Default kitchen
                                })
                    # Handle jika items adalah Dict (Legacy Format)
                    elif isinstance(items, dict):
                        for k, v in items.items():
                             flat_menu_data.append({
                                    "Kategori": category,
                                    "Nama Menu": k,
                                    "Harga": float(v.get('price', 0)),
                                    "Printer": v.get('printer', 'kitchen')
                                })

            # Jika data kosong, kasih template row biar user ga bingung
            if not flat_menu_data:
                flat_menu_data.append({"Kategori": "FOOD", "Nama Menu": "Contoh Menu Baru", "Harga": 15000, "Printer": "kitchen"})

            # Buat DataFrame
            df_editor_source = pd.DataFrame(flat_menu_data)

            # 2. SHOW EDITOR
            edited_df = st.data_editor(
                df_editor_source,
                num_rows="dynamic", # Membolehkan user tambah/hapus baris
                use_container_width=True,
                column_config={
                    "Harga": st.column_config.NumberColumn(
                        "Harga (Rp)",
                        format="Rp %d",
                        min_value=0
                    ),
                    "Kategori": st.column_config.SelectboxColumn(
                        "Kategori",
                        options=["FOOD", "BEVERAGE", "SNACK", "OTHERS", "PAKET"], # Opsi bisa disesuaikan
                        required=True
                    ),
                    "Printer": st.column_config.SelectboxColumn(
                        "Target Printer",
                        options=["kitchen", "bar", "cashier"],
                        required=True
                    )
                },
                hide_index=True
            )

            # 3. SAVE BUTTON LOGIC
            col_act1, col_act2 = st.columns([1, 4])
            with col_act1:
                save_btn = st.button("💾 Simpan Perubahan ke Cloud", type="primary", use_container_width=True)
            
            if save_btn:
                # Convert DataFrame kembali ke struktur Dictionary Menu
                # Struktur: { "Kategori": [ {name, price, printer}, ... ] }
                
                new_menu_dict = {}
                try:
                    for index, row in edited_df.iterrows():
                        cat = row['Kategori'].strip() if row['Kategori'] else "OTHERS"
                        name = row['Nama Menu'].strip()
                        price = float(row['Harga'])
                        printer = row['Printer']
                        
                        # Validasi nama tidak boleh kosong
                        if not name: continue
                        
                        if cat not in new_menu_dict:
                            new_menu_dict[cat] = []
                        
                        new_menu_dict[cat].append({
                            "name": name,
                            "price": price,
                            "printer": printer
                        })
                    
                    # Upload ke Firestore
                    with st.spinner("Menyimpan ke Cloud..."):
                        success, msg = save_menu_config_to_cloud(selected_branch, new_menu_dict)
                    
                    if success:
                        st.success(f"✅ {msg}")
                        time.sleep(1.5)
                        st.rerun() # Refresh agar data terupdate
                    else:
                        st.error(f"❌ {msg}")
                        
                except Exception as e:
                    st.error(f"Terjadi kesalahan saat memproses data: {e}")
                    st.write("Debug Error:", e)
