import re

with open('/opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index/html/wechat_account_settings.html', 'r', encoding='utf-8') as f:
    content = f.read()

# The new CSS to inject
NEW_CSS = """
        :root {
            /* Background Colors - Soft Pearl / Neutral Ash */
            --bg-main: #E2E8F0;
            
            /* Glassmorphism Variables - Balanced Medium Mode */
            --glass-bg: rgba(255, 255, 255, 0.45);
            --glass-bg-hover: rgba(255, 255, 255, 0.6);
            --glass-border: rgba(255, 255, 255, 0.5);
            --glass-highlight: rgba(255, 255, 255, 0.8);
            --glass-input-bg: rgba(255, 255, 255, 0.35);
            
            /* Text Colors */
            --text-primary: #334155;
            --text-secondary: #64748B;
            --text-muted: #94A3B8;
            
            /* Shapes */
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
            
            /* Status Colors */
            --status-success: #0F766E;
            --status-error: #B91C1C;
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
            font-family: 'Inter', 'Source Han Sans CN', 'PingFang SC', sans-serif;
        }

        body {
            background-color: var(--bg-main);
            color: var(--text-primary);
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
            padding: 40px 24px;
        }

        /* Abstract blurred background shapes - Desaturated & Calmer */
        .bg-shape {
            position: absolute;
            border-radius: 50%;
            filter: blur(120px);
            z-index: -1;
            opacity: 0.45; /* Lowered opacity */
            pointer-events: none;
        }
        .shape-1 { top: -10%; left: -5%; width: 500px; height: 500px; background: #7DD3FC; }
        .shape-2 { bottom: -15%; right: -5%; width: 600px; height: 600px; background: #99F6E4; }
        .shape-3 { top: 30%; left: 40%; width: 400px; height: 400px; background: #C4B5FD; opacity: 0.35; }

        .container {
            max-width: 1240px;
            margin: 0 auto;
            position: relative;
            z-index: 1;
        }

        .topbar {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 16px;
            margin-bottom: 32px;
            color: var(--text-primary);
            padding: 0 8px;
        }

        .topbar h1 {
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 6px;
        }

        .topbar p {
            color: var(--text-secondary);
            font-size: 14px;
        }

        .topbar-actions {
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }

        .btn {
            background: rgba(255, 255, 255, 0.5);
            border: 1px solid var(--glass-border);
            color: var(--text-primary);
            padding: 10px 20px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03);
            text-decoration: none;
        }

        .btn:hover { 
            background: rgba(255, 255, 255, 0.8); 
            box-shadow: 0 2px 6px rgba(0,0,0,0.05);
            transform: translateY(-1px);
        }

        .btn-primary {
            background: #1D4ED8;
            border-color: #1D4ED8;
            color: #FFFFFF;
            box-shadow: 0 4px 12px rgba(29, 78, 216, 0.15);
        }

        .btn-primary:hover { 
            background: #1E40AF; 
            border-color: #1E40AF; 
            box-shadow: 0 6px 16px rgba(29, 78, 216, 0.25); 
            color: #FFFFFF;
        }

        .btn-danger {
            background: #B91C1C;
            color: white;
            border-color: #B91C1C;
        }
        
        .btn-danger:hover {
            background: #991B1B;
            color: white;
        }
        
        .btn-text-danger {
            background: rgba(254, 242, 242, 0.5);
            color: #B91C1C;
            border: 1px solid rgba(254, 202, 202, 0.5);
            padding: 6px 12px;
            font-size: 12px;
        }
        
        .btn-text-danger:hover {
            background: #FEF2F2;
        }

        .layout {
            display: grid;
            grid-template-columns: 360px 1fr;
            gap: 24px;
        }

        .panel {
            background: var(--glass-bg);
            backdrop-filter: blur(24px);
            -webkit-backdrop-filter: blur(24px);
            border: 1px solid var(--glass-border);
            border-top-color: var(--glass-highlight);
            border-left-color: var(--glass-highlight);
            border-radius: var(--radius-lg);
            box-shadow: 0 4px 24px rgba(15, 23, 42, 0.06);
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }

        .panel-header {
            padding: 24px 24px 16px;
            border-bottom: 1px solid rgba(0,0,0,0.05);
        }

        .panel-header h2 {
            font-size: 18px;
            margin-bottom: 8px;
            font-weight: 700;
        }

        .panel-header p {
            color: var(--text-secondary);
            font-size: 13px;
            line-height: 1.6;
        }

        .account-list {
            padding: 16px;
            max-height: 70vh;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        /* Custom Scrollbar */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.15); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.25); }

        .account-card {
            padding: 16px;
            border-radius: var(--radius-md);
            border: 1px solid var(--glass-border);
            background: rgba(255, 255, 255, 0.3);
            cursor: pointer;
            transition: all 0.2s ease;
            box-shadow: 0 2px 8px rgba(0,0,0,0.02);
        }

        .account-card:hover,
        .account-card.active {
            border-color: #3B82F6;
            background: rgba(255, 255, 255, 0.7);
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08);
        }

        .account-card-title {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }

        .account-card-title strong {
            font-size: 15px;
            font-weight: 600;
            color: var(--text-primary);
        }

        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
            background: rgba(29, 78, 216, 0.1);
            color: #1D4ED8;
            white-space: nowrap;
        }

        .account-card-meta {
            color: var(--text-secondary);
            font-size: 13px;
            line-height: 1.6;
            word-break: break-all;
        }

        .empty-state {
            padding: 36px 24px;
            color: var(--text-secondary);
            text-align: center;
            line-height: 1.8;
            font-size: 14px;
        }

        .form-body {
            padding: 24px;
            overflow-y: auto;
        }

        .alert {
            display: none;
            margin: 0 24px 0;
            padding: 14px 16px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            font-weight: 500;
            line-height: 1.6;
        }

        .alert.show {
            display: block;
        }

        .alert-success {
            background: rgba(15, 118, 110, 0.1);
            color: var(--status-success);
            border: 1px solid rgba(15, 118, 110, 0.2);
        }

        .alert-error {
            background: rgba(185, 28, 28, 0.1);
            color: var(--status-error);
            border: 1px solid rgba(185, 28, 28, 0.2);
        }

        .form-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 20px;
        }

        .field {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }

        .field.full {
            grid-column: 1 / -1;
        }

        .field label {
            font-size: 13px;
            font-weight: 600;
            color: var(--text-secondary);
        }

        .field input,
        .field textarea,
        .field select {
            width: 100%;
            background: var(--glass-input-bg);
            border: 1px solid var(--glass-border);
            border-radius: var(--radius-sm);
            padding: 12px 14px;
            font-size: 14px;
            color: var(--text-primary);
            outline: none;
            transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.02);
        }

        .field textarea {
            min-height: 100px;
            resize: vertical;
            line-height: 1.6;
        }

        .field input:focus,
        .field textarea:focus,
        .field select:focus {
            background: rgba(255, 255, 255, 0.7);
            border-color: #3B82F6;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
        }

        .field small {
            color: var(--text-muted);
            font-size: 12px;
            line-height: 1.5;
            margin-top: 2px;
        }

        .section-title {
            grid-column: 1 / -1;
            padding-top: 16px;
            padding-bottom: 8px;
            font-size: 16px;
            font-weight: 700;
            color: var(--text-primary);
            border-bottom: 1px solid rgba(0,0,0,0.05);
            margin-bottom: 8px;
        }

        .option-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }

        .option-item {
            display: flex;
            align-items: flex-start;
            gap: 10px;
            padding: 12px 14px;
            border: 1px solid var(--glass-border);
            border-radius: var(--radius-sm);
            background: rgba(255, 255, 255, 0.4);
            cursor: pointer;
            line-height: 1.4;
            font-size: 13px;
            transition: background 0.2s;
        }
        
        .option-item:hover {
            background: rgba(255, 255, 255, 0.7);
        }

        .option-item input {
            margin-top: 2px;
        }

        .option-empty {
            color: var(--text-muted);
            padding: 12px 14px;
            border: 1px dashed rgba(0,0,0,0.1);
            border-radius: var(--radius-sm);
            background: rgba(0,0,0,0.02);
            font-size: 13px;
        }

        .checkbox-row {
            display: flex;
            align-items: center;
            gap: 10px;
            margin-top: 6px;
            color: var(--text-primary);
            font-weight: 500;
            font-size: 14px;
            cursor: pointer;
        }

        .form-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 32px;
            padding-top: 24px;
            border-top: 1px solid rgba(0,0,0,0.05);
        }

        .info-box {
            margin-top: 24px;
            padding: 16px;
            background: rgba(59, 130, 246, 0.08);
            border-radius: var(--radius-md);
            font-size: 13px;
            color: #1E3A8A;
            line-height: 1.7;
            border: 1px solid rgba(59, 130, 246, 0.15);
        }

        .keyword-response-actions {
            display: flex;
            justify-content: flex-start;
            margin-bottom: 12px;
        }

        .keyword-response-list {
            display: flex;
            flex-direction: column;
            gap: 16px;
        }

        .keyword-response-item {
            background: rgba(255,255,255,0.4);
            border: 1px solid var(--glass-border);
            border-radius: var(--radius-md);
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.02);
        }

        .keyword-response-item-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }

        .keyword-response-item-title {
            font-size: 14px;
            font-weight: 700;
            color: var(--text-primary);
        }

        .keyword-response-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
        }

        .keyword-response-grid .field.full {
            grid-column: 1 / -1;
        }

        @media (max-width: 960px) {
            .layout {
                grid-template-columns: 1fr;
            }
            .form-grid {
                grid-template-columns: 1fr;
            }
            .option-grid {
                grid-template-columns: 1fr;
            }
            .keyword-response-grid {
                grid-template-columns: 1fr;
            }
        }
"""

new_content = re.sub(r'<style>.*?</style>', f'<style>\n{NEW_CSS}\n    </style>', content, flags=re.DOTALL)

# Add the bg shapes just after <body>
bg_shapes = """<body>
    <div class="bg-shape shape-1"></div>
    <div class="bg-shape shape-2"></div>
    <div class="bg-shape shape-3"></div>"""

new_content = new_content.replace('<body>', bg_shapes)

# Change topbar-actions buttons mapping if needed (optional)
# We already covered .btn-primary etc in CSS.

with open('/opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index/html/wechat_account_settings.html', 'w', encoding='utf-8') as f:
    f.write(new_content)
