import os
import urllib.parse
from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
import requests
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from collections import Counter

# PostgreSQL 전용 드라이버 및 커서 로드
import psycopg2
import psycopg2.extras

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "fallback_secret_key_for_dev")

NAVER_CLIENT_ID = os.environ.get("tX4C62FCAHTajdV2WVmu")
NAVER_CLIENT_SECRET = os.environ.get("WP_3dx3rrF")

# 💡 환경 변수에서 콜백 주소를 읽어오되, 없을 경우에만 로컬 주소를 기본값으로 사용
NAVER_REDIRECT_URI = os.environ.get("NAVER_REDIRECT_URI", "http://127.0.0.1:5001/login/naver/callback")

TTB_KEY = os.environ.get("TTB_KEY")
NLK_API_KEY = os.environ.get("NLK_API_KEY", "38df841a00dd6f304ac12fe83f501b83a396d92b520d512dda2413ee2442405d")

# 클라우드 환경변수에서 DB URL 주입
DATABASE_URL = os.environ.get("DATABASE_URL")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = "error"
login_manager.login_message = "해당 기능은 로그인이 필요합니다."

def get_db_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL 환경 변수가 설정되지 않았습니다.")
    conn = psycopg2.connect(DATABASE_URL)
    return conn

class User(UserMixin):
    def __init__(self, id, username, nickname=None, theme='light'):
        self.id = id
        self.username = username
        self.nickname = nickname if nickname else username
        self.theme = theme

@login_manager.user_loader
def load_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute("SELECT id, username, nickname, theme FROM users WHERE id = %s", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    if user_data:
        theme_val = user_data['theme'] if user_data['theme'] else 'light'
        return User(id=user_data['id'], username=user_data['username'], nickname=user_data['nickname'], theme=theme_val)
    return None

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # PostgreSQL 표준 문법(SERIAL)을 적용한 스키마 정의
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                nickname VARCHAR(255),
                theme VARCHAR(50) DEFAULT 'light'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                isbn VARCHAR(255),
                title VARCHAR(255),
                author VARCHAR(255),
                cover VARCHAR(255),
                review_text TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                rating INTEGER DEFAULT 5,
                kdc_class VARCHAR(100) DEFAULT '미분류'
            )
        ''')
        conn.commit()
    except Exception as e:
        print(f"Database Initialization Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

# 애플리케이션 시작 시 DB 초기화 강제 실행
init_db()

def get_kdc_name(kdc_code):
    if not kdc_code: return None
    first_digit = str(kdc_code)[0]
    kdc_map = {
        '0': '총류 (사전/컴퓨터)', '1': '철학 (심리학/윤리)', '2': '종교',
        '3': '사회과학 (경제/정치/사회)', '4': '자연과학 (수학/물리/생물)',
        '5': '기술과학 (의학/공학)', '6': '예술 (음악/미술/체육)',
        '7': '언어', '8': '문학 (소설/시/에세이)', '9': '역사 (지리/위인전)'
    }
    return kdc_map.get(first_digit, None)

def guess_kdc_from_category(cat_name):
    if not cat_name: return "미분류"
    if "컴퓨터" in cat_name or "IT" in cat_name: return "총류 (사전/컴퓨터)"
    if "철학" in cat_name or "인문" in cat_name: return "철학 (심리학/윤리)"
    if "사회" in cat_name or "경제" in cat_name or "정치" in cat_name: return "사회과학 (경제/정치/사회)"
    if "과학" in cat_name or "수학" in cat_name: return "자연과학 (수학/물리/생물)"
    if "기술" in cat_name or "공학" in cat_name: return "기술과학 (의학/공학)"
    if "예술" in cat_name or "체육" in cat_name: return "예술 (음악/미술/체육)"
    if "언어" in cat_name or "외국어" in cat_name: return "언어"
    if "소설" in cat_name or "시" in cat_name or "에세이" in cat_name: return "문학 (소설/시/에세이)"
    if "역사" in cat_name or "지리" in cat_name: return "역사 (지리/위인전)"
    return "미분류"

@app.route('/api/kdc/<isbn>')
def api_kdc(isbn):
    aladin_category = request.args.get('category', '')
    kdc_name = None
    if NLK_API_KEY != "여기에_발급받으신_국립중앙도서관_키를_넣어주세요":
        try:
            url_nlk = "https://www.nl.go.kr/NL/search/openApi/search.do"
            params_nlk = {"key": NLK_API_KEY, "detailSearch": "true", "isbnOp": "isbn", "isbnCode": isbn}
            nlk_response = requests.get(url_nlk, params=params_nlk, timeout=2) 
            root = ET.fromstring(nlk_response.content)
            for item in root.findall('.//item'):
                kdc = item.findtext('kdc')
                if kdc:
                    kdc_name = get_kdc_name(kdc)
                    break
        except Exception:
            pass
    if not kdc_name:
        kdc_name = guess_kdc_from_category(aladin_category)
    return jsonify({"kdc_class": kdc_name})

@app.route('/api/update_theme', methods=['POST'])
@login_required
def update_theme():
    data = request.get_json()
    new_theme = data.get('theme')
    if new_theme in ['light', 'dark']:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET theme = %s WHERE id = %s", (new_theme, current_user.id))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"})
    return jsonify({"status": "error"}), 400

@app.route('/login/naver')
def login_naver():
    if not NAVER_CLIENT_ID:
        flash("서버 환경 변수에 네이버 API 키가 설정되지 않았습니다.", "error")
        return redirect(url_for('login'))
        
    state = os.urandom(16).hex()
    session['naver_state'] = state
    auth_url = f"https://nid.naver.com/oauth2.0/authorize?response_type=code&client_id={NAVER_CLIENT_ID}&redirect_uri={urllib.parse.quote(NAVER_REDIRECT_URI)}&state={state}"
    return redirect(auth_url)

@app.route('/login/naver/callback')
def naver_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    
    token_url = f"https://nid.naver.com/oauth2.0/token?grant_type=authorization_code&client_id={NAVER_CLIENT_ID}&client_secret={NAVER_CLIENT_SECRET}&code={code}&state={state}"
    token_res = requests.get(token_url).json()
    access_token = token_res.get('access_token')

    if not access_token:
        flash("네이버 로그인 인증에 실패했습니다.", "error")
        return redirect(url_for('login'))

    header = "Bearer " + access_token
    profile_url = "https://openapi.naver.com/v1/nid/me"
    profile_res = requests.get(profile_url, headers={'Authorization': header}).json()

    if profile_res.get('resultcode') == '00':
        naver_user = profile_res.get('response')
        naver_id = naver_user.get('id')
        nickname = naver_user.get('nickname') or "네이버유저"
        username = f"naver_{naver_id[:8]}"

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT id, username, nickname, theme FROM users WHERE username = %s", (username,))
        user_data = cursor.fetchone()

        if not user_data:
            dummy_pw = generate_password_hash(os.urandom(16).hex())
            # PostgreSQL에서 삽입된 ID를 가져오기 위해 RETURNING 구문 사용
            cursor.execute(
                "INSERT INTO users (username, password_hash, nickname, theme) VALUES (%s, %s, %s, 'light') RETURNING id", 
                (username, dummy_pw, nickname)
            )
            user_id = cursor.fetchone()['id']
            conn.commit()
            db_username = username
            db_nickname = nickname
            db_theme = 'light'
        else:
            user_id = user_data['id']
            db_username = user_data['username']
            db_nickname = user_data['nickname']
            db_theme = user_data['theme'] if user_data['theme'] else 'light'
        conn.close()

        user = User(id=user_id, username=db_username, nickname=db_nickname, theme=db_theme)
        login_user(user, remember=True)
        flash(f"{user.nickname} 작가님, 환영합니다!", "success")
        return redirect(url_for('home'))
    else:
        flash("네이버 프로필 정보를 가져오지 못했습니다.", "error")
        return redirect(url_for('login'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = %s", (username,))
        if cursor.fetchone():
            conn.close()
            flash("이미 존재하는 아이디입니다.", "error")
            return redirect(url_for('signup'))
        hashed_pw = generate_password_hash(password)
        cursor.execute("INSERT INTO users (username, password_hash, nickname, theme) VALUES (%s, %s, %s, 'light')", (username, hashed_pw, username))
        conn.commit()
        conn.close()
        flash("회원가입이 완료되었습니다. 로그인을 진행해 주세요.", "success")
        return redirect(url_for('login'))
    return render_template('signup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = True if request.form.get('remember') else False 
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute("SELECT id, username, password_hash, nickname, theme FROM users WHERE username = %s", (username,))
        user_data = cursor.fetchone()
        conn.close()
        if user_data and check_password_hash(user_data['password_hash'], password):
            user = User(id=user_data['id'], username=user_data['username'], nickname=user_data['nickname'], theme=user_data['theme'])
            login_user(user, remember=remember) 
            flash(f"{user.nickname} 작가님, 환영합니다!", "success")
            return redirect(url_for('home'))
        else:
            flash("아이디 또는 비밀번호가 틀렸습니다.", "error")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash("안전하게 로그아웃되었습니다.", "info")
    return redirect(url_for('home'))

@app.route('/', methods=['GET', 'POST'])
def home():
    grouped_books = {}  
    search_term = ""
    error_msg = None
    selected_publisher = request.form.get('publisher', '전체')
    publisher_list = [] 
    
    if not TTB_KEY:
        return render_template('index.html', error="서버 환경 변수에 알라딘 API 키가 설정되지 않았습니다.")

    if request.method == 'POST':
        search_term = request.form.get('search_query')
        url = "http://www.aladin.co.kr/ttb/api/ItemSearch.aspx"
        all_items = []
        max_pages = 5 
        for page in range(1, max_pages + 1):
            params = {
                "ttbkey": TTB_KEY, "Query": search_term, "QueryType": "Title", 
                "MaxResults": 50, "start": page, "SearchTarget": "Book", 
                "Sort": "SalesPoint", "output": "js", "Version": "20131101"
            }
            try:
                response = requests.get(url, params=params, timeout=5).json()
                if 'item' in response and len(response['item']) > 0:
                    all_items.extend(response['item'])
                    if len(response['item']) < 50: break
                else: break
            except Exception as e:
                break
        if len(all_items) > 0:
            all_publishers = set(item.get('publisher', '').strip() for item in all_items if item.get('publisher', '').strip())
            publisher_list = sorted(list(all_publishers))
            for item in all_items:
                book_publisher = item.get('publisher', '').strip()
                if selected_publisher != '전체' and selected_publisher != book_publisher: continue
                title = item.get('title')
                isbn = item.get('isbn13')
                raw_author = item.get('author', '')
                match = re.search(r'\s*[:|-]|\s+\d+', title)
                base_title = title[:match.start()].strip() if match else f"{title}_{isbn}"
                main_author = raw_author.split(',')[0].strip()
                group_key = f"{base_title}_{main_author}"
                book_data = {'title': title, 'author': raw_author, 'publisher': book_publisher, 'isbn': isbn, 'cover': item.get('cover'), 'description': item.get('description', '줄거리 정보가 없습니다.')}
                if group_key not in grouped_books: grouped_books[group_key] = [book_data]
                else:
                    if not any(b['isbn'] == isbn for b in grouped_books[group_key]):
                        grouped_books[group_key].append(book_data)
            for key in grouped_books:
                grouped_books[key] = sorted(grouped_books[key], key=lambda x: [int(text) if text.isdigit() else text for text in re.split(r'(\d+)', x['title'])])
            if not grouped_books: error_msg = f"'{selected_publisher}' 출판사 결과가 없습니다."
        else: error_msg = "검색 결과가 없습니다."
    return render_template('index.html', grouped_books=grouped_books, search_term=search_term, error=error_msg, selected_publisher=selected_publisher, publisher_list=publisher_list)

@app.route('/book/<isbn>')
def book_detail(isbn):
    if not TTB_KEY:
        return "알라딘 API 키가 설정되지 않았습니다.", 500
        
    url_aladin = "http://www.aladin.co.kr/ttb/api/ItemLookUp.aspx"
    params_aladin = {"ttbkey": TTB_KEY, "ItemId": isbn, "ItemIdType": "ISBN13", "output": "js", "Version": "20131101"}
    aladin_response = requests.get(url_aladin, params=params_aladin).json()
    if 'item' not in aladin_response or len(aladin_response['item']) == 0: return "책 정보를 찾을 수 없습니다.", 404
    book_info = aladin_response['item'][0]
    existing_reviews = []
    if current_user.is_authenticated:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cursor.execute('SELECT * FROM reviews WHERE isbn = %s AND user_id = %s ORDER BY created_at DESC', (isbn, current_user.id))
        existing_reviews = cursor.fetchall()
        conn.close()
    return render_template('book_detail.html', book=book_info, reviews=existing_reviews)

@app.route('/save_review', methods=['POST'])
@login_required 
def save_review():
    isbn = request.form.get('isbn')
    title = request.form.get('title')
    author = request.form.get('author')
    cover = request.form.get('cover')
    review_text = request.form.get('review_text')
    rating = int(request.form.get('rating', 5))
    kdc_class = request.form.get('kdc_class', '미분류')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO reviews (user_id, isbn, title, author, cover, review_text, rating, kdc_class) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''', (current_user.id, isbn, title, author, cover, review_text, rating, kdc_class))
    conn.commit()
    conn.close()
    flash("기록이 도서 보드에 안전하게 보관되었습니다.", "success")
    return redirect(url_for('book_detail', isbn=isbn))

@app.route('/my_reviews')
@login_required 
def my_reviews():
    sort_by = request.args.get('sort', 'latest')
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    base_query = 'SELECT * FROM reviews WHERE user_id = %s '
    if sort_by == 'rating_high': base_query += 'ORDER BY rating DESC, created_at DESC'
    elif sort_by == 'rating_low': base_query += 'ORDER BY rating ASC, created_at DESC'
    else: base_query += 'ORDER BY created_at DESC'
    cursor.execute(base_query, (current_user.id,))
    reviews = cursor.fetchall()
    conn.close()
    total_count = len(reviews)
    month_count = 0
    top_author = "-"
    avg_rating = 0.0
    kdc_stats = {}
    if total_count > 0:
        current_month = datetime.now().strftime('%Y-%m')
        month_count = sum(1 for r in reviews if r['created_at'] and str(r['created_at']).startswith(current_month))
        authors = [r['author'].split(',')[0].split('(')[0].strip() for r in reviews if r['author']]
        if authors: top_author = Counter(authors).most_common(1)[0][0]
        avg_rating = round(sum(r['rating'] for r in reviews) / total_count, 1)
        kdc_list = [r['kdc_class'] for r in reviews]
        kdc_stats = dict(Counter(kdc_list))
    return render_template('reviews.html', reviews=reviews, total_count=total_count, month_count=month_count, top_author=top_author, avg_rating=avg_rating, kdc_stats=kdc_stats, current_sort=sort_by)

@app.route('/edit_review/<int:review_id>', methods=['GET', 'POST'])
@login_required
def edit_review(review_id):
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    if request.method == 'POST':
        updated_text = request.form.get('review_text')
        updated_rating = int(request.form.get('rating', 5))
        cursor.execute('UPDATE reviews SET review_text = %s, rating = %s WHERE id = %s AND user_id = %s', (updated_text, updated_rating, review_id, current_user.id))
        conn.commit()
        conn.close()
        flash("감상문이 성공적으로 수정되었습니다.", "success")
        return redirect(url_for('my_reviews'))
    else:
        cursor.execute('SELECT * FROM reviews WHERE id = %s AND user_id = %s', (review_id, current_user.id))
        review = cursor.fetchall()
        conn.close()
        if not review: return "권한이 없거나 해당 기록을 찾을 수 없습니다.", 404
        return render_template('edit_review.html', review=review[0])

@app.route('/delete_review', methods=['POST'])
@login_required
def delete_review():
    review_id = request.form.get('review_id')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM reviews WHERE id = %s AND user_id = %s', (review_id, current_user.id))
    conn.commit()
    conn.close()
    flash("독서 기록이 삭제되었습니다.", "info")
    return redirect(url_for('my_reviews'))

@app.route('/delete_all_reviews', methods=['POST'])
@login_required
def delete_all_reviews():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM reviews WHERE user_id = %s', (current_user.id,))
    conn.commit()
    conn.close()
    flash("모든 독서 기록이 초기화되었습니다.", "info")
    return redirect(url_for('my_reviews'))

@app.route('/statistics')
@login_required
def statistics():
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cursor.execute('SELECT * FROM reviews WHERE user_id = %s ORDER BY created_at ASC', (current_user.id,))
    reviews = cursor.fetchall()
    conn.close()
    total_count = len(reviews)
    if total_count == 0: return render_template('statistics.html', empty=True)
    kdc_list = [r['kdc_class'] for r in reviews]
    kdc_stats = dict(Counter(kdc_list))
    monthly_counts = {}
    for r in reviews:
        if r['created_at']:
            month = str(r['created_at'])[:7] 
            monthly_counts[month] = monthly_counts.get(month, 0) + 1
    sorted_months = sorted(monthly_counts.keys())
    monthly_stats = {m: monthly_counts[m] for m in sorted_months}
    avg_rating = round(sum(r['rating'] for r in reviews) / total_count, 1)
    return render_template('statistics.html', empty=False, total_count=total_count, avg_rating=avg_rating, kdc_stats=kdc_stats, monthly_stats=monthly_stats)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        action = request.form.get('action')
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if action == 'update_nickname':
            new_nickname = request.form.get('new_nickname')
            cursor.execute("UPDATE users SET nickname = %s WHERE id = %s", (new_nickname, current_user.id))
            conn.commit()
            current_user.nickname = new_nickname 
            flash("프로필 정보가 업데이트되었습니다.", "success")
            
        elif action == 'change_password':
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            cursor.execute("SELECT password_hash FROM users WHERE id = %s", (current_user.id,))
            user_data = cursor.fetchone()
            if user_data and check_password_hash(user_data['password_hash'], current_password):
                new_hashed = generate_password_hash(new_password)
                cursor.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hashed, current_user.id))
                conn.commit()
                flash("비밀번호가 업데이트되었습니다.", "success")
            else:
                flash("현재 비밀번호가 일치하지 않습니다.", "error")
                
        elif action == 'delete_account':
            cursor.execute("DELETE FROM reviews WHERE user_id = %s", (current_user.id,))
            cursor.execute("DELETE FROM users WHERE id = %s", (current_user.id,))
            conn.commit()
            conn.close()
            logout_user()
            flash("계정이 정상적으로 탈퇴 처리되었습니다.", "info")
            return redirect(url_for('home'))
            
        conn.close()
        return redirect(url_for('settings'))
    return render_template('settings.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port)
