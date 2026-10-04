from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 青森市に対応する市区町村コード。実際の気象庁データでは市名でも照合できるよう、
# コードと地域名の両方を許容する。
AREA_CODE = "022010"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
NOTIFICATION_HISTORY_FILE = os.path.join(APP_DIR, 'data', 'notification_history.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
notification_history = load_json(NOTIFICATION_HISTORY_FILE, [])

DEFAULT_SHELTER_COORDS = {
    '御所見小学校': {'lat': 40.8395, 'lng': 140.7380},
    '片瀬小学校': {'lat': 40.8160, 'lng': 140.7120},
    '鵠洋小学校': {'lat': 40.8010, 'lng': 140.7455},
    'あああ': {'lat': 40.8255, 'lng': 140.7585},
    'いいい': {'lat': 40.8183, 'lng': 140.7605},
    '青森市中心部': {'lat': 40.8242, 'lng': 140.7415},
}


def normalize_shelter_data(items):
    """避難所データに位置情報を補完して返す"""
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue

        shelter_name = item.get('name', '').strip()
        coords = DEFAULT_SHELTER_COORDS.get(shelter_name)

        result = dict(item)
        result['status'] = '開設中' if result.get('status') == '開設中' else '未開設'
        if coords:
            result['lat'] = coords['lat']
            result['lng'] = coords['lng']
            result['latitude'] = coords['lat']
            result['longitude'] = coords['lng']
        elif 'lat' not in result and 'latitude' not in result:
            result['lat'] = 40.8242
            result['lng'] = 140.7415
            result['latitude'] = 40.8242
            result['longitude'] = 140.7415

        normalized.append(result)

    return normalized

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_resident_notices():
    """ホーム画面に表示する有効な住民向け指示を新しい順に返す"""
    inactive_statuses = {'解除', '完了', '取消', '終了', '停止'}
    urgent_values = {'緊急', '高', '高い', '重要', 'emergency', 'critical', 'high'}
    urgent_terms = ('避難指示', '緊急', '至急', '避難してください', '直ちに', '危険')
    notices = []

    for instruction in instructions:
        if instruction.get('target') != '住民':
            continue
        if instruction.get('status', '').strip() in inactive_statuses:
            continue

        notice = dict(instruction)
        priority = str(
            instruction.get('urgency')
            or instruction.get('priority')
            or instruction.get('severity')
            or ''
        ).strip().lower()
        content = instruction.get('content', '')
        notice['is_urgent'] = (
            priority in urgent_values
            or any(term in content for term in urgent_terms)
        )
        notices.append(notice)

    notices.sort(
        key=lambda notice: parse_japanese_datetime(
            notice.get('created_at') or notice.get('updated_at')
        ),
        reverse=True
    )
    return notices
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def parse_japanese_datetime(value):
    """日本語時刻文字列を datetime に変換する"""
    if not value:
        return datetime.min.replace(tzinfo=JST)
    try:
        return datetime.strptime(value, "%Y年%m月%d日 %H:%M").replace(tzinfo=JST)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y年%m月%d日 %H:%M:%S").replace(tzinfo=JST)
        except ValueError:
            return datetime.min.replace(tzinfo=JST)


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    results = [s for s in shelters if not district or s.get('district') == district]
    return normalize_shelter_data(results)


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and (
                    item.get("areaCode") == AREA_CODE
                    or item.get("areaName") == AREA_NAME
                    or item.get("name") == AREA_NAME
                )
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = get_resident_notices()
    return render_template('index.html', resident_notices=resident_notices)

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        shelter_name = request.form.get('name', '').strip()
        shelter_status = request.form.get('status', '未開設')
        if shelter_status not in ('開設中', '未開設'):
            shelter_status = '未開設'

        if not shelter_name:
            return render_template(
                'shelter_register.html',
                error=True,
                message='避難所名を入力してください。'
            )

        # 既に同じ名前が存在する場合は重複登録を防ぐ
        if any(s.get('name') == shelter_name for s in shelters):
            return render_template(
                'shelter_register.html',
                error=True,
                message='同じ避難所名は登録できません。'
            )

        new_id = max((s.get('id', 0) for s in shelters), default=0) + 1
        shelters.append({
            'id': new_id,
            'name': shelter_name,
            'lat': 40.8242,
            'lng': 140.7415,
            'latitude': 40.8242,
            'longitude': 140.7415,
            'status': shelter_status,
        })

        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(shelters, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所を登録しました。'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    return render_template('shelter_search.html')

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)


def get_disaster_events(filter_type='all'):
    """災害情報リストを生成し、新しい順に並べる"""
    events = []
    for item in notification_history:
        category = '情報'
        if item.get('has_emergency'):
            category = '緊急'
        elif item.get('has_warning') or item.get('has_advisory'):
            category = '警報'

        if item.get('warning_count', 0) > 0 or item.get('warnings'):
            category = '警報'

        summary = item.get('area_name', '市内')
        if item.get('warnings'):
            warning_names = ', '.join(w.get('name', '') for w in item.get('warnings', []) if w.get('name'))
            summary = warning_names or '市内の災害情報'
        else:
            summary = '災害情報の確認が必要です'

        icon = '🚨' if category == '緊急' else '⚠️' if category == '警報' else 'ℹ️'
        event = {
            'id': item.get('timestamp', ''),
            'icon': icon,
            'category': category,
            'summary': summary,
            'received_at': item.get('timestamp', ''),
            'sort_key': parse_japanese_datetime(item.get('timestamp')),
            'area_name': item.get('area_name', '市内')
        }

        if filter_type == 'all' or filter_type == category:
            events.append(event)

    events.sort(key=lambda item: item['sort_key'], reverse=True)
    return events


# 指示ボード：住民向けの指示と災害情報を一覧で確認する
@app.route('/board')
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    selected_type = request.args.get('type', 'all')
    disaster_events = get_disaster_events(selected_type)
    return render_template(
        'board.html',
        instructions=resident_instructions,
        disaster_events=disaster_events,
        current_filter=selected_type,
        filter_options=['all', '緊急', '警報', '情報']
    )

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
