# File: config.py

# ==============================================================================
# --- KONFIGURASI CABANG AKTIF ---
# Ubah nilai di bawah ini untuk berganti antar restoran.
NAMA_CABANG = "HOKEE_KG"
# ==============================================================================

# Data unik untuk setiap cabang
RESTAURANT_CONFIG = {
    "COLEGA_PIK": {
        "restaurant_name": "Colega Chocolate & Cafe",
        "address": "Beach Theme Park Blok E No.33-35\nPantai Indah Kapuk, Jakarta 14470",
        "phone": "+62 812 8889 8980",
        "unique_code_prefix": "CC",
        "service_charge_rate": 0.05,
        "pb1_rate": 0.10,

        # [BARU] Setting Lebar Kertas Printer
        # 48 = Printer Besar (80mm) - Standar Kasir
        # 32 = Printer Kecil (58mm) - Portable/Bluetooth
        "printer_paper_width": 48, 

        "cashier_printer_name": "CASHIER", 
        "bar_printer_name": "BAR",
        "kitchen_printer_name": "KITCHEN",
        "pastries_printer_name": "PASTRIES",
        "server_printer_name": "SERVER",

        "table_layout": {
            "tabs": {
                "Meja Utama": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16],
                "Meja X": [f"X{i}" for i in range(1, 8)]
            },
            "layout_config": {}
        }
    },
    "HOKEE_PIK": {
        "restaurant_name": "HOKEE\nHong Kong Cafe",
        "address": "Beach Theme Park Blok E No. 173-177\nPantai Indah Kapuk, Jakarta 14470",
        "phone": "+62 888 8108 000",
        "unique_code_prefix": "HK",
        "service_charge_rate": 0.05,
        "pb1_rate": 0.10,

        # [BARU] Setting Lebar Kertas Printer
        "printer_paper_width": 48, 

        "cashier_printer_name": "CASHIER", 
        "bar_printer_name": "BAR",
        "kitchen_printer_name": "KITCHEN",
        "pastries_printer_name": "PASTRIES",
        "server_printer_name": "SERVER",

        "table_layout": {
            "tabs": {
                "Meja 1-30": list(range(1, 18)),
                "Meja 31-60": list(range(31, 61)),
                "Meja X": [f"X{i}" for i in range(1, 8)]
            },
            "layout_config": { "columns_per_tab": 6 }
        }
    },
    "Testing": {
        "restaurant_name": "Testing Restoran",
        "address": "Beach Theme Park Blok E No. 173-177\nPantai Indah Kapuk, Jakarta 14470",
        "phone": "+62 888 8108 000",
        "unique_code_prefix": "TS",
        "service_charge_rate": 0.05,
        "pb1_rate": 0.10,

        # [BARU] Setting Lebar Kertas Printer
        "printer_paper_width": 48, 

        "cashier_printer_name": "CASHIER", 
        "bar_printer_name": "BAR",
        "kitchen_printer_name": "KITCHEN",
        "pastries_printer_name": "PASTRIES",
        "server_printer_name": "SERVER",

        "table_layout": {
            "tabs": {
                "Meja 1-30": list(range(1, 31)),
                "Meja 31-60": list(range(31, 61)),
                "Meja X": [f"X{i}" for i in range(1, 8)]
            },
            "layout_config": { "columns_per_tab": 6 }
        }
    },
    "HOKEE_KG": {
        "restaurant_name": "HOKEE\nHong Kong Cafe",
        "address": "K MALL, NO. LG-18 KOMPLEK,Gn SAHARI SELATAN, KEMAYORAN, Jakarta 10610",
        "phone": "08888080500",
        "unique_code_prefix": "KG",
        "service_charge_rate": 0.05,
        "pb1_rate": 0.10,

        # [BARU] Setting Lebar Kertas Printer
        "printer_paper_width": 32, # Contoh cabang ini pakai printer kecil

        "cashier_printer_name": "CASHIER", 
        "bar_printer_name": "BAR",
        "kitchen_printer_name": "KITCHEN",
        "pastries_printer_name": "PASTRIES",
        "server_printer_name": "SERVER",

        "table_layout": {
            "tabs": {
                "Indoor": list(range(1, 22)),
                "Outdoor": [34, 33, 32, 31, 38, 37, 36, 35, 25, 26, 27, 28, 29, 30, 24],
                "Meja Tambahan": list(range(40, 81)),
                "Meja X": [f"X{i}" for i in range(1, 8)]
            },
            "layout_config": { "columns_per_tab": 8 }
        }
    }
}

# --- Ambil Konfigurasi Aktif ---
ACTIVE_CONFIG = RESTAURANT_CONFIG[NAMA_CABANG]