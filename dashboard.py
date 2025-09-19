import streamlit as st
import pandas as pd
import firebase_admin
from firebase_admin import credentials, db
from io import BytesIO
from datetime import datetime
import altair as alt
import json

# --- KONFIGURASI ---
DATABASE_URL = 'https://management-asset-1ca79-default-rtdb.asia-southeast1.firebasedatabase.app/'
CREDENTIALS_FILE = 'firebase-credentials.json'

# --- Inisialisasi Firebase ---
@st.cache_resource
def initialize_firebase():
    try:
        if not firebase_admin._apps:
            # Ambil kredensial dari Streamlit Secrets, bukan dari file
            creds_json_str = st.secrets["firebase_credentials"]
            creds_dict = json.loads(creds_json_str)

            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred, {
                'databaseURL': 'https://management-asset-1ca79-default-rtdb.asia-southeast1.firebasedatabase.app/'
            })
    except Exception as e:
        st.error(f"Gagal terhubung ke Firebase. Pastikan Anda sudah mengatur Secrets dengan benar.")
        st.stop()

# --- Fungsi untuk Mengambil Data ---
@st.cache_data(ttl=60) # TTL (Time to Live) diubah menjadi 60 detik untuk data yang lebih real-time
def fetch_data(branch_name):
    try:
        ref = db.reference(f'/{branch_name}/order_history')
        data = ref.get()
        if not data:
            return []
        # Mengambil data dari Firebase yang mungkin memiliki key unik
        if isinstance(data, dict):
            return list(data.values())
        # Jika data sudah berupa list (jarang terjadi tapi untuk keamanan)
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        st.error(f"Gagal mengambil data untuk {branch_name}: {e}")
        return []

# --- Fungsi Helper untuk Menghitung Total ---
def calculate_grand_total(subtotal, discount_str="0"):
    subtotal = float(subtotal)
    service = subtotal * 0.05
    pb1_base = subtotal + service
    tax = pb1_base * 0.10
    try:
        percentage = float(str(discount_str).split(" ", 1)[0])
    except:
        percentage = 0
    discount_amount = subtotal * (percentage / 100)
    total_final = subtotal + service + tax - discount_amount
    return {'subtotal': subtotal, 'tax': tax, 'service': service, 'discount': discount_amount, 'total': total_final}

# --- Fungsi untuk memproses data mentah ---
def process_data_for_display(history_data):
    processed_records = []
    for order in history_data:
        if isinstance(order, dict) and order.get('status') == 'completed':
            items_list = order.get('items_in_payment', order.get('items', []))
            subtotal = sum(item.get('price', 0) * item.get('quantity', 1) for item in items_list)
            totals = calculate_grand_total(subtotal, order.get('discount', '0'))
            item_details = "; ".join([f"{item['quantity']}x {item['name']}" for item in items_list])
            payment_details = "; ".join([f"{p['method']}: {p.get('amount', 0):,.0f}" for p in order.get('payments', [])])

            timestamp = order.get('timestamp')
            if timestamp:
                try:
                    order_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    record = {
                        "Kode Unik": order.get('unique_code', 'N/A'),
                        "Tanggal": order_time.date(),
                        "Waktu": order_time.time(),
                        "Tipe Order": order.get('order_type', 'N/A'),
                        "Meja": order.get('table', 'N/A'),
                        "Kasir": order.get('user', 'N/A'),
                        "Subtotal": totals['subtotal'],
                        "Diskon": totals['discount'],
                        "Service (5%)": totals['service'],
                        "Pajak (10%)": totals['tax'],
                        "Grand Total": totals['total'],
                        "Metode Bayar": payment_details,
                        "Detail Item": item_details
                    }
                    processed_records.append(record)
                except ValueError:
                    # Lewati data jika format timestamp salah
                    continue
    return pd.DataFrame(processed_records)


# --- Tampilan Utama Aplikasi Streamlit ---
st.set_page_config(layout="wide", page_title="Dashboard Monitoring")
st.title("📊 Dashboard Monitoring Restoran")
st.markdown("Pantau semua transaksi dari seluruh cabang secara real-time.")

initialize_firebase()

branches = ["COLEGA_PIK", "HOKEE_PIK"]
selected_branch = st.selectbox("Pilih Cabang Restoran untuk Dipantau:", branches)

if selected_branch:
    with st.spinner(f"Memuat data transaksi dari {selected_branch}..."):
        history_data = fetch_data(selected_branch)

    if not history_data:
        st.warning("Belum ada data transaksi untuk cabang ini.")
    else:
        df_all = process_data_for_display(history_data)

        if df_all.empty:
            st.warning("Tidak ada transaksi yang lunas (completed) untuk ditampilkan.")
        else:
            # --- FILTER TANGGAL (Diletakkan di luar tab agar berlaku untuk semua) ---
            st.header(f"Filter Laporan untuk: {selected_branch}")
            
            min_date = df_all['Tanggal'].min()
            max_date = df_all['Tanggal'].max()

            col_filter1, col_filter2 = st.columns(2)
            with col_filter1:
                start_date = st.date_input("Dari Tanggal", value=min_date, min_value=min_date, max_value=max_date)
            with col_filter2:
                end_date = st.date_input("Sampai Tanggal", value=max_date, min_value=min_date, max_value=max_date)

            # Terapkan filter ke DataFrame
            df_filtered = df_all[(df_all['Tanggal'] >= start_date) & (df_all['Tanggal'] <= end_date)]

            if df_filtered.empty:
                st.info("Tidak ada data pada rentang tanggal yang dipilih.")
            else:
                # --- PEMBUATAN TAB ---
                tab1, tab2 = st.tabs(["📊 Ringkasan & KPI", "📄 Rincian Transaksi"])

                # --- ISI TAB 1: RINGKASAN & KPI ---
                with tab1:
                    st.subheader("Ringkasan Performa")
                    total_omset = df_filtered['Grand Total'].sum()
                    total_transaksi = len(df_filtered)
                    rata_rata_transaksi = total_omset / total_transaksi if total_transaksi > 0 else 0
                    
                    col1, col2, col3 = st.columns(3)
                    col1.metric("💰 Total Omset", f"Rp {total_omset:,.0f}")
                    col2.metric("🧾 Jumlah Transaksi", f"{total_transaksi}")
                    col3.metric("🛒 Rata-rata per Transaksi", f"Rp {rata_rata_transaksi:,.0f}")
                    
                    st.divider()
                    
                    st.subheader("Visualisasi Data")
                    col_chart1, col_chart2 = st.columns(2)

                    with col_chart1:
                        # Grafik Tren Penjualan Harian
                        st.markdown("##### Tren Penjualan Harian")
                        sales_over_time = df_filtered.groupby('Tanggal')['Grand Total'].sum().reset_index()
                        line_chart = alt.Chart(sales_over_time).mark_line(point=True).encode(
                            x=alt.X('Tanggal:T', title='Tanggal'),
                            y=alt.Y('Grand Total:Q', title='Total Omset (Rp)'),
                            tooltip=[alt.Tooltip('Tanggal', title='Tanggal'), alt.Tooltip('Grand Total', title='Omset', format=',.0f')]
                        ).interactive()
                        st.altair_chart(line_chart, use_container_width=True)

                    with col_chart2:
                        # Grafik Penjualan per Tipe Order
                        st.markdown("##### Omset per Tipe Order")
                        sales_by_type = df_filtered.groupby('Tipe Order')['Grand Total'].sum().sort_values(ascending=False).reset_index()
                        bar_chart = alt.Chart(sales_by_type).mark_bar().encode(
                            x=alt.X('Grand Total:Q', title='Total Omset (Rp)'),
                            y=alt.Y('Tipe Order:N', title='Tipe Order', sort='-x'),
                            tooltip=[alt.Tooltip('Tipe Order', title='Tipe'), alt.Tooltip('Grand Total', title='Omset', format=',.0f')]
                        ).interactive()
                        st.altair_chart(bar_chart, use_container_width=True)


                # --- ISI TAB 2: RINCIAN TRANSAKSI ---
                with tab2:
                    st.subheader("Tabel Rincian Semua Transaksi")

                    # Tombol Export Excel (tidak berubah)
                    output = BytesIO()
                    df_to_export = df_filtered.copy()
                    df_to_export['Tanggal'] = df_to_export['Tanggal'].astype(str)
                    df_to_export['Waktu'] = df_to_export['Waktu'].astype(str)

                    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                        df_to_export.to_excel(writer, index=False, sheet_name='Transaksi')
                        writer.sheets['Transaksi'].autofit()
                    
                    st.download_button(
                        label="📥 Download Laporan Excel (sesuai filter)",
                        data=output.getvalue(),
                        file_name=f"laporan_{selected_branch}_{start_date}_sd_{end_date}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

                    # --- PERBAIKAN: Format angka secara manual sebelum ditampilkan ---
                    df_display = df_filtered.copy()
                    kolom_uang = ["Subtotal", "Diskon", "Service (5%)", "Pajak (10%)", "Grand Total"]

                    for kolom in kolom_uang:
                        # Mengubah angka menjadi string dengan format Rupiah dan pemisah titik
                        # Contoh: 96000 -> "Rp 96.000"
                        df_display[kolom] = df_display[kolom].apply(lambda x: f"Rp {x:,.0f}".replace(',', '.'))

                    # Tampilkan DataFrame yang sudah diformat (tanpa column_config)
                    st.dataframe(df_display, use_container_width=True)


