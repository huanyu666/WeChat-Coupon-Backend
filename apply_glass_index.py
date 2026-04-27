import os
import re

FILES = ['index.html']

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

        .panel, .login-container, .card, .glass-card, .header {
            background: var(--glass-bg) !important;
            backdrop-filter: blur(24px) !important;
            -webkit-backdrop-filter: blur(24px) !important;
            border: 1px solid var(--glass-border) !important;
            border-top-color: var(--glass-highlight) !important;
            border-left-color: var(--glass-highlight) !important;
            border-radius: var(--radius-lg) !important;
            box-shadow: 0 4px 24px rgba(15, 23, 42, 0.06) !important;
        }
        
        .header, .topbar { color: var(--text-primary) !important; }
        .header-left h1 { color: var(--text-primary) !important; font-weight: 700 !important; text-shadow: none !important; }
        .header-left p, .user-info { color: var(--text-secondary) !important; text-shadow: none !important; }
        
        .card-icon { background: rgba(59, 130, 246, 0.1) !important; color: #1D4ED8 !important; }
        .card-title { color: var(--text-primary) !important; }
        .card-desc { color: var(--text-secondary) !important; }
        .btn-enter { background: #1D4ED8 !important; color: #FFFFFF !important; border: none !important; box-shadow: 0 4px 12px rgba(29, 78, 216, 0.15) !important; }
        .btn-enter:hover { background: #1E40AF !important; box-shadow: 0 6px 16px rgba(29, 78, 216, 0.25) !important; transform: translateY(-1px) !important; }
        
        .btn-logout { background: rgba(254, 242, 242, 0.5) !important; color: #B91C1C !important; border: 1px solid rgba(254, 202, 202, 0.5) !important; }
        .btn-logout:hover { background: #FEF2F2 !important; }
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
        
    if 'bg-shape shape-1' in content:
        print(f"Skipping {file_name}, already applied.")
        continue

    content = re.sub(r'(<style[^>]*>)', r'\1\n' + CSS_VARS, content, count=1)
    
    idx = content.rfind('</style>')
    if idx != -1:
        content = content[:idx] + CSS_OVERRIDES + content[idx:]
        
    content = re.sub(r'(<body[^>]*>)', r'\1\n' + SHAPES, content, count=1)
    
    if 'fonts.googleapis.com' not in content:
        content = content.replace('</head>', '    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">\n</head>')

    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
        
    print(f"Updated {file_name}")

