import os
import re

FILES = [
    'login.html',
    'wechat_account_settings.html',
    'material_upload.html',
    'order-rankings.html'
]

CSS_VARS = """
        :root {
            --bg-main: #E2E8F0;
            --glass-bg: rgba(255, 255, 255, 0.45);
            --glass-bg-hover: rgba(255, 255, 255, 0.6);
            --glass-border: rgba(255, 255, 255, 0.5);
            --glass-highlight: rgba(255, 255, 255, 0.8);
            --glass-input-bg: rgba(255, 255, 255, 0.35);
            --text-primary: #334155;
            --text-secondary: #64748B;
            --text-muted: #94A3B8;
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
        }
"""

CSS_OVERRIDES = """
        body {
            background: var(--bg-main) !important;
            color: var(--text-primary) !important;
            font-family: 'Inter', 'Source Han Sans CN', 'PingFang SC', sans-serif !important;
        }
        
        .bg-shape {
            position: fixed;
            border-radius: 50%;
            filter: blur(120px);
            z-index: -1;
            opacity: 0.45;
            pointer-events: none;
        }
        .shape-1 { top: -10%; left: -5%; width: 500px; height: 500px; background: #7DD3FC; }
        .shape-2 { bottom: -15%; right: -5%; width: 600px; height: 600px; background: #99F6E4; }
        .shape-3 { top: 30%; left: 40%; width: 400px; height: 400px; background: #C4B5FD; opacity: 0.35; }

        .panel, .login-container, .card, .glass-card {
            background: var(--glass-bg) !important;
            backdrop-filter: blur(24px) !important;
            -webkit-backdrop-filter: blur(24px) !important;
            border: 1px solid var(--glass-border) !important;
            border-top-color: var(--glass-highlight) !important;
            border-left-color: var(--glass-highlight) !important;
            border-radius: var(--radius-lg) !important;
            box-shadow: 0 4px 24px rgba(15, 23, 42, 0.06) !important;
        }
        
        .header, .topbar { color: var(--text-primary) !important; background: transparent !important; }
        .header h1, .topbar h1 { color: var(--text-primary) !important; font-weight: 700 !important; text-shadow: none !important; }
        .header p, .topbar p { color: var(--text-secondary) !important; text-shadow: none !important; }
        .login-title { color: var(--text-primary) !important; }
        
        .btn-primary, .btn-login {
            background: #1D4ED8 !important;
            border-color: #1D4ED8 !important;
            color: #FFFFFF !important;
            box-shadow: 0 4px 12px rgba(29, 78, 216, 0.15) !important;
        }
        .btn-primary:hover, .btn-login:hover {
            background: #1E40AF !important;
            box-shadow: 0 6px 16px rgba(29, 78, 216, 0.25) !important;
            transform: translateY(-1px) !important;
        }
        
        .btn-secondary, .btn-default, .btn:not(.btn-primary):not(.btn-danger) {
            background: rgba(255, 255, 255, 0.5) !important;
            border: 1px solid var(--glass-border) !important;
            color: var(--text-primary) !important;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03) !important;
        }
        .btn-secondary:hover, .btn-default:hover, .btn:not(.btn-primary):not(.btn-danger):hover {
            background: rgba(255, 255, 255, 0.8) !important;
            box-shadow: 0 2px 6px rgba(0,0,0,0.05) !important;
            transform: translateY(-1px) !important;
        }
        
        input, select, textarea, .form-input, .form-select {
            background: var(--glass-input-bg) !important;
            border: 1px solid var(--glass-border) !important;
            color: var(--text-primary) !important;
            box-shadow: inset 0 2px 4px rgba(0,0,0,0.02) !important;
        }
        input:focus, select:focus, textarea:focus, .form-input:focus, .form-select:focus {
            background: rgba(255, 255, 255, 0.7) !important;
            border-color: #3B82F6 !important;
            box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1) !important;
        }
        
        .account-card, .list-item, .option-item {
            background: rgba(255, 255, 255, 0.3) !important;
            border: 1px solid var(--glass-border) !important;
            color: var(--text-primary) !important;
        }
        .account-card:hover, .account-card.active, .list-item:hover, .option-item:hover {
            background: rgba(255, 255, 255, 0.7) !important;
            border-color: #3B82F6 !important;
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.08) !important;
        }
        
        .panel-header { border-bottom: 1px solid rgba(0,0,0,0.05) !important; }
        .card-title { color: var(--text-primary) !important; }
        .form-label, .field label { color: var(--text-secondary) !important; }
        .alert-success { background: rgba(15, 118, 110, 0.1) !important; color: #0F766E !important; border: 1px solid rgba(15, 118, 110, 0.2) !important; }
        .alert-error { background: rgba(185, 28, 28, 0.1) !important; color: #B91C1C !important; border: 1px solid rgba(185, 28, 28, 0.2) !important; }
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.15); border-radius: 10px; }
        ::-webkit-scrollbar-thumb:hover { background: rgba(0,0,0,0.25); }
"""

SHAPES = """
    <div class="bg-shape shape-1"></div>
    <div class="bg-shape shape-2"></div>
    <div class="bg-shape shape-3"></div>
"""

base_dir = '/opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index/html'

for file_name in FILES:
    path = os.path.join(base_dir, file_name)
    if not os.path.exists(path):
        continue
        
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Check if already applied to prevent double applying
    if 'bg-shape shape-1' in content:
        print(f"Skipping {file_name}, already applied.")
        continue

    # Insert CSS Variables after <style>
    content = re.sub(r'(<style[^>]*>)', r'\1\n' + CSS_VARS, content, count=1)
    
    # Insert CSS Overrides before </style>
    # Note: we find the LAST </style> tag if there are multiple.
    idx = content.rfind('</style>')
    if idx != -1:
        content = content[:idx] + CSS_OVERRIDES + content[idx:]
        
    # Insert Shapes after <body>
    content = re.sub(r'(<body[^>]*>)', r'\1\n' + SHAPES, content, count=1)
    
    # Optionally, remove Google Fonts if already present, and add Inter font
    if 'fonts.googleapis.com' not in content:
        content = content.replace('</head>', '    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">\n</head>')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print(f"Updated {file_name}")

