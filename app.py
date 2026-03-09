from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_file
# Try psycopg2 first, fall back to pg8000
try:
    import psycopg2
    from psycopg2 import Error
    from psycopg2.extras import RealDictCursor
    USING_PSYCOPG2 = True
except ImportError:
    import pg8000
    from pg8000 import DatabaseError as Error
    # pg8000 doesn't have RealDictCursor exactly, but we can use native connections
    USING_PSYCOPG2 = False
    print("Using pg8000 as database adapter")
import razorpay
import json
import hmac
import hashlib
import time
import os

app = Flask(__name__)

app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())

# ================= RAZORPAY CONFIGURATION =================
RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID')
RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET')
if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET:
    razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
else:
    razorpay_client = None
    print("WARNING: Razorpay keys not set in environment variables")

# ================= POSTGRESQL CONNECTION =================
# ================= POSTGRESQL CONNECTION =================
def get_db_connection():
    try:
        if USING_PSYCOPG2:
            conn = psycopg2.connect(
                host=os.environ.get('DB_HOST'),
                database=os.environ.get('DB_NAME'),
                user=os.environ.get('DB_USER'),
                password=os.environ.get('DB_PASSWORD'),
                port=os.environ.get('DB_PORT', '5432'),
                sslmode='require'  # Add SSL for psycopg2
            )
        else:
            conn = pg8000.connect(
                host=os.environ.get('DB_HOST'),
                database=os.environ.get('DB_NAME'),
                user=os.environ.get('DB_USER'),
                password=os.environ.get('DB_PASSWORD'),
                port=int(os.environ.get('DB_PORT', '5432')),
                ssl_context=True  # Enable SSL for pg8000
            )
        return conn
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return None
    
if os.environ.get('FLASK_ENV') == 'production':
    app.config['DEBUG'] = False
    app.config['TESTING'] = False
    # Security settings for production
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['REMEMBER_COOKIE_SECURE'] = True
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True

@app.route('/')
def home():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT m.mid, m.mname, m.price, fc.category, qm.size
        FROM menu m
        JOIN food_cat fc ON fc.fid = m.fid
        JOIN qty_mast qm ON qm.qid = m.qid
        ORDER BY m.mid
    """)

    menus = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("public_menu.html", menus=menus)

@app.route('/admin/complete_order/<int:oid>')
def complete_order(oid):
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE orders
        SET status='Completed'
        WHERE oid=%s
    """, (oid,))

    conn.commit()
    cur.close()
    conn.close()

    flash("Order marked as Completed!", "success")
    return redirect('/admin/orders')
    pass

@app.route('/cancel_order/<int:oid>')
def cancel_order(oid):
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT status FROM orders
        WHERE oid=%s AND user_id=%s
    """, (oid, session['user_id']))

    order = cur.fetchone()

    if order and order[0] == 'Pending':
        cur.execute("""
            UPDATE orders
            SET status='Cancelled'
            WHERE oid=%s
        """, (oid,))
        conn.commit()
        flash("Order Cancelled Successfully!", "success")
    else:
        flash("Cannot cancel this order!", "error")

    cur.close()
    conn.close()

    return redirect('/my_orders')

@app.route('/admin/reject_order/<int:oid>')
def reject_order(oid):
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE orders
        SET status='Rejected'
        WHERE oid=%s
    """, (oid,))

    conn.commit()
    cur.close()
    conn.close()

    flash("Order Rejected!", "info")
    return redirect('/admin/orders')

@app.route('/order_details/<int:oid>')
def order_details(oid):
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor()

    # Get order details with timestamps
    cur.execute("""
        SELECT o.oid, o.total_price, o.status, o.payment_method, 
               o.payment_status, o.table_number,
               TO_CHAR(o.created_at, 'DD/MM/YYYY HH12:MI AM') as order_date,
               TO_CHAR(o.updated_at, 'DD/MM/YYYY HH12:MI AM') as last_updated
        FROM orders o
        WHERE o.oid=%s
    """, (oid,))
    
    order = cur.fetchone()

    # Get order items
    cur.execute("""
        SELECT m.mname, oi.qty, oi.price
        FROM order_items oi
        JOIN menu m ON m.mid = oi.menu_id
        WHERE oi.order_id=%s
    """, (oid,))

    items = cur.fetchall()
    
    # Calculate total
    total = 0
    for item in items:
        total += item[1] * item[2]
    
    cur.close()
    conn.close()

    return render_template("order_details.html", items=items, total=total, order=order)

@app.route('/admin/dashboard')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM orders")
    total_orders = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status='Completed'")
    total_sales = cur.fetchone()[0] or 0

    cur.close()
    conn.close()

    return render_template("admin_dashboard.html",
                           total_orders=total_orders,
                           total_sales=total_sales)

from flask import Response

@app.route('/invoice/<int:oid>')
def invoice(oid):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT m.mname, oi.qty, oi.price
        FROM order_items oi
        JOIN menu m ON m.mid = oi.menu_id
        WHERE oi.order_id=%s
    """, (oid,))

    items = cur.fetchall()
    cur.close()
    conn.close()

    content = "Invoice\n\n"
    total = 0
    for item in items:
        line = f"{item[0]} x {item[1]} = ₹ {item[2]*item[1]}\n"
        content += line
        total += item[2]*item[1]

    content += f"\nTotal: ₹ {total}"

    return Response(
        content,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment;filename=invoice_{oid}.txt"}
    )

@app.route('/dashboard')
def dashboard():
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM orders")
    total_orders = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status='Completed'")
    total_revenue = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM orders WHERE status='Pending'")
    pending_orders = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM orders WHERE status='Pending Payment'")
    pending_payment_orders = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM menu")
    total_menu = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM food_cat")
    total_categories = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM qty_mast")
    total_sizes = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM users WHERE role='viewer'")
    total_viewers = cur.fetchone()[0]
    
    cur.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    total_admins = cur.fetchone()[0]
    
    cur.execute("""
        SELECT id, username, email, role, created_at
        FROM users 
        ORDER BY id DESC 
        LIMIT 5
    """)
    recent_users = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
        "dashboard.html",
        total_orders=total_orders,
        total_revenue=total_revenue,
        pending_orders=pending_orders,
        pending_payment_orders=pending_payment_orders,
        total_menu=total_menu,
        total_categories=total_categories,
        total_sizes=total_sizes,
        total_users=total_users,
        total_viewers=total_viewers,
        total_admins=total_admins,
        recent_users=recent_users
    )


@app.route('/sitemap.xml')
def sitemap():
    return send_file('sitemap.xml', mimetype='application/xml')


@app.route('/users')
def users():
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, username, email, role, created_at
        FROM users 
        ORDER BY id DESC
    """)
    
    users = cur.fetchall()
    cur.close()
    conn.close()
    
    return render_template("users.html", users=users)

@app.route('/user/delete/<int:user_id>')
def delete_user(user_id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    if user_id == session['user_id']:
        flash("You cannot delete your own account!", "error")
        return redirect('/users')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=%s", (user_id,))
    order_count = cur.fetchone()[0]
    
    if order_count > 0:
        cur.execute("DELETE FROM orders WHERE user_id=%s", (user_id,))
    
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    conn.commit()
    
    cur.close()
    conn.close()
    
    flash("User deleted successfully!", "success")
    return redirect('/users')

@app.route('/user/toggle_role/<int:user_id>')
def toggle_role(user_id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    if user_id == session['user_id']:
        flash("You cannot change your own role!", "error")
        return redirect('/users')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT role FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    
    if user:
        new_role = 'admin' if user[0] == 'viewer' else 'viewer'
        cur.execute("UPDATE users SET role=%s WHERE id=%s", (new_role, user_id))
        conn.commit()
        flash(f"User role updated to {new_role}!")
    
    cur.close()
    conn.close()
    
    return redirect('/users')

@app.route('/profile')
def profile():
    if 'user_id' not in session:
        flash("Please login first!", "info")
        return redirect('/login')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id, username, email, role, table_number FROM users WHERE id=%s", (session['user_id'],))
    user = cur.fetchone()
    
    cur.execute("SELECT COUNT(*) FROM orders WHERE user_id=%s", (session['user_id'],))
    order_count = cur.fetchone()[0]
    
    cur.execute("SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE user_id=%s AND status='Completed'", (session['user_id'],))
    total_spent = cur.fetchone()[0]
    
    cur.close()
    conn.close()
    
    return render_template("profile.html", user=user, order_count=order_count, total_spent=total_spent)

@app.route('/change_password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        flash("Please login first!", "info")
        return redirect('/login')
    
    if request.method == 'POST':
        current_password = request.form['current_password']
        new_password = request.form['new_password']
        confirm_password = request.form['confirm_password']
        
        if new_password != confirm_password:
            flash("New passwords do not match!", "info")
            return redirect('/change_password')
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT password FROM users WHERE id=%s", (session['user_id'],))
        user = cur.fetchone()
        
        if user and check_password_hash(user[0], current_password):
            hashed_password = generate_password_hash(new_password)
            cur.execute("UPDATE users SET password=%s WHERE id=%s", (hashed_password, session['user_id']))
            conn.commit()
            flash("Password changed successfully!", "success")
            cur.close()
            conn.close()
            return redirect('/profile')
        else:
            flash("Current password is incorrect!", "error")
            cur.close()
            conn.close()
            return redirect('/change_password')
    
    return render_template("change_password.html")

@app.route('/delete_my_account', methods=['POST'])
def delete_my_account():
    if 'user_id' not in session:
        flash("Please login first!", "info")
        return redirect('/login')
    
    if session.get('role') == 'admin':
        flash("Admins cannot delete their account. Please contact another admin!", "error")
        return redirect('/profile')
    
    password = request.form['password']
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT password FROM users WHERE id=%s", (session['user_id'],))
    user = cur.fetchone()
    
    if user and check_password_hash(user[0], password):
        cur.execute("UPDATE orders SET status='Cancelled' WHERE user_id=%s", (session['user_id'],))
        cur.execute("DELETE FROM users WHERE id=%s", (session['user_id'],))
        conn.commit()
        
        cur.close()
        conn.close()
        
        session.clear()
        flash("Your account has been deleted successfully!", "success")
        return redirect('/')
    else:
        flash("Incorrect password!", "error")
        cur.close()
        conn.close()
        return redirect('/profile')

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user_id' not in session:
        flash("Please login first!", "info")
        return redirect('/login')
    
    username = request.form['username']
    email = request.form['email']
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT id FROM users WHERE username=%s AND id!=%s", (username, session['user_id']))
    if cur.fetchone():
        flash("Username already taken!", "info")
        cur.close()
        conn.close()
        return redirect('/profile')
    
    cur.execute("SELECT id FROM users WHERE email=%s AND id!=%s", (email, session['user_id']))
    if cur.fetchone():
        flash("Email already registered!", "info")
        cur.close()
        conn.close()
        return redirect('/profile')
    
    cur.execute("UPDATE users SET username=%s, email=%s WHERE id=%s", (username, email, session['user_id']))
    conn.commit()
    
    session['username'] = username
    
    cur.close()
    conn.close()
    
    flash("Profile updated successfully!", "success")
    return redirect('/profile')

@app.route('/update_cart/<int:mid>/<string:action>')
def update_cart(mid, action):
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')

    cart = session.get('cart', {})
    mid_str = str(mid)

    if mid_str in cart:
        if action == 'increase':
            cart[mid_str] += 1
            flash("Quantity increased!", "info")
        elif action == 'decrease':
            if cart[mid_str] > 1:
                cart[mid_str] -= 1
                flash("Quantity decreased!", "info")
            else:
                del cart[mid_str]
                flash("Item removed from cart!", "info")
        elif action == 'remove':
            del cart[mid_str]
            flash("Item removed from cart!", "info")
    else:
        flash("Item not in cart!", "warning")

    session['cart'] = cart
    return redirect('/cart')

@app.route('/clear_cart')
def clear_cart():
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')
    
    session.pop('cart', None)
    flash("Cart cleared!", "info")
    return redirect('/cart')

@app.route('/categories', methods=['GET', 'POST'])
def categories():
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        category = request.form['category']
        cur.execute("INSERT INTO food_cat (category) VALUES (%s)", (category,))
        conn.commit()
        flash("Category added successfully!", "success")

    cur.execute("SELECT * FROM food_cat ORDER BY fid")
    categories = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("categories.html", categories=categories)

@app.route('/category/edit/<int:id>', methods=['GET', 'POST'])
def edit_category(id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        category = request.form['category']
        cur.execute("UPDATE food_cat SET category=%s WHERE fid=%s",
                    (category, id))
        conn.commit()
        flash("Category updated!", "success")
        return redirect('/categories')

    cur.execute("SELECT * FROM food_cat WHERE fid=%s", (id,))
    category = cur.fetchone()

    cur.close()
    conn.close()

    return render_template("edit_category.html", category=category)

@app.route('/category/delete/<int:id>')
def delete_category(id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("DELETE FROM food_cat WHERE fid=%s", (id,))
        conn.commit()
        flash("Category deleted!", "success")
    except Exception as e:
        conn.rollback()
        flash("Cannot delete category as it is being used by menu items!", "error")

    cur.close()
    conn.close()
    return redirect('/categories')

@app.route('/quantities', methods=['GET', 'POST'])
def quantities():
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        size = request.form['size']
        cur.execute("INSERT INTO qty_mast (size) VALUES (%s)", (size,))
        conn.commit()
        flash("Size added successfully!", "success")

    cur.execute("SELECT * FROM qty_mast ORDER BY qid")
    quantities = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("quantities.html", quantities=quantities)

@app.route('/quantity/edit/<int:id>', methods=['GET', 'POST'])
def edit_quantity(id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        size = request.form['size']
        cur.execute("UPDATE qty_mast SET size=%s WHERE qid=%s",
                    (size, id))
        conn.commit()
        flash("Size updated!", "success")
        return redirect('/quantities')

    cur.execute("SELECT * FROM qty_mast WHERE qid=%s", (id,))
    quantity = cur.fetchone()

    cur.close()
    conn.close()

    return render_template("edit_quantity.html", quantity=quantity)

@app.route('/quantity/delete/<int:id>')
def delete_quantity(id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("DELETE FROM qty_mast WHERE qid=%s", (id,))
        conn.commit()
        flash("Size deleted!", "success")
    except Exception as e:
        conn.rollback()
        flash("Cannot delete size as it is being used by menu items!", "error")

    cur.close()
    conn.close()
    return redirect('/quantities')

@app.route('/menu')
def menu_list():
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT m.mid, m.mname, m.price, fc.category, qm.size
        FROM menu m
        JOIN food_cat fc ON fc.fid = m.fid
        JOIN qty_mast qm ON qm.qid = m.qid
        ORDER BY m.mid
    """)

    menus = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("menu_list.html", menus=menus)

@app.route('/menu/add', methods=['GET', 'POST'])
def add_menu():
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        mname = request.form['mname']
        price = request.form['price']
        fid = request.form['fid']
        qid = request.form['qid']

        cur.execute("""
            INSERT INTO menu (mname, price, fid, qid)
            VALUES (%s, %s, %s, %s)
        """, (mname, price, fid, qid))

        conn.commit()
        flash("Menu item added successfully!", "success")
        return redirect(url_for('menu_list'))

    cur.execute("SELECT * FROM food_cat ORDER BY fid")
    categories = cur.fetchall()

    cur.execute("SELECT * FROM qty_mast ORDER BY qid")
    quantities = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("add_menu.html",
                           categories=categories,
                           quantities=quantities)

@app.route('/menu/edit/<int:id>', methods=['GET', 'POST'])
def edit_menu(id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        mname = request.form['mname']
        price = request.form['price']
        fid = request.form['fid']
        qid = request.form['qid']

        cur.execute("""
            UPDATE menu
            SET mname=%s, price=%s, fid=%s, qid=%s
            WHERE mid=%s
        """, (mname, price, fid, qid, id))

        conn.commit()
        flash("Menu item updated successfully!", "success")
        return redirect(url_for('menu_list'))

    cur.execute("SELECT * FROM menu WHERE mid=%s", (id,))
    menu = cur.fetchone()

    cur.execute("SELECT * FROM food_cat ORDER BY fid")
    categories = cur.fetchall()

    cur.execute("SELECT * FROM qty_mast ORDER BY qid")
    quantities = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("edit_menu.html",
                           menu=menu,
                           categories=categories,
                           quantities=quantities)

@app.route('/menu/delete/<int:id>')
def delete_menu(id):
    if 'role' not in session or session['role'] != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        cur.execute("DELETE FROM menu WHERE mid=%s", (id,))
        conn.commit()
        flash("Menu item deleted!", "success")
    except Exception as e:
        conn.rollback()
        flash("Cannot delete menu item as it is in orders!", "error")

    cur.close()
    conn.close()
    return redirect(url_for('menu_list'))

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        try:
            cur.execute("""
                INSERT INTO users (username, email, password)
                VALUES (%s, %s, %s)
            """, (username, email, password))
            conn.commit()
            flash("Account created successfully!", "success")
            return redirect('/login')
        except Exception as e:
            # Check if it's an integrity error
            if 'unique' in str(e).lower() or 'duplicate' in str(e).lower():
                flash("Username or email already exists!", "error")
            else:
                flash(f"Error creating account: {e}", "error")
            conn.rollback()
            flash("Username or email already exists!", "error")

    cur.close()
    conn.close()
    return render_template("signup.html")

@app.route('/login', methods=['GET','POST'])
def login():
    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        cur.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cur.fetchone()

        if user and check_password_hash(user[3], password):
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[4]
            flash("Login Successful", "success")
            return redirect('/')
        else:
            flash("Invalid Credentials", "error")

    cur.close()
    conn.close()
    return render_template("login.html")

@app.route('/delete_account', methods=['POST'])
def delete_account():
    if 'user_id' not in session:
        flash("You must be logged in!", "info")
        return redirect('/login')

    if session.get('role') == 'admin':
        flash("Admin account cannot be deleted here!", "success")
        return redirect('/dashboard')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM users WHERE id=%s", (session['user_id'],))
    conn.commit()

    cur.close()
    conn.close()

    session.clear()
    flash("Your account has been deleted successfully!", "success")
    return redirect('/')

@app.route('/add_to_cart/<int:mid>')
def add_to_cart(mid):
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')

    if 'cart' not in session:
        session['cart'] = {}

    cart = session['cart']

    if str(mid) in cart:
        cart[str(mid)] += 1
    else:
        cart[str(mid)] = 1

    session['cart'] = cart
    flash("Item added to cart!", "success")
    return redirect('/')

@app.route('/cart')
def view_cart():
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')

    cart = session.get('cart', {})
    items = []
    total = 0

    conn = get_db_connection()
    cur = conn.cursor()

    for mid, qty in cart.items():
        cur.execute("SELECT mid, mname, price FROM menu WHERE mid=%s", (mid,))
        menu = cur.fetchone()

        if menu:
            subtotal = menu[2] * qty
            total += subtotal

            items.append({
                'mid': menu[0],
                'name': menu[1],
                'price': menu[2],
                'qty': qty,
                'subtotal': subtotal
            })

    cur.close()
    conn.close()

    return render_template("cart.html", items=items, total=total)

@app.route('/remove_from_cart/<int:mid>')
def remove_from_cart(mid):
    cart = session.get('cart', {})

    if str(mid) in cart:
        del cart[str(mid)]

    session['cart'] = cart
    flash("Item removed!", "info")
    return redirect('/cart')

@app.route('/my_orders')
def my_orders():
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT oid, total_price, status, payment_method, payment_status, 
               COALESCE(payment_error, '') as payment_error,
               TO_CHAR(created_at, 'DD/MM/YYYY HH12:MI AM') as order_date,
               TO_CHAR(updated_at, 'DD/MM/YYYY HH12:MI AM') as last_updated
        FROM orders
        WHERE user_id=%s
        ORDER BY oid DESC
    """, (session['user_id'],))

    orders = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("my_orders.html", orders=orders)

@app.route('/admin/orders')
def admin_orders():
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT o.oid, u.username, o.total_price, o.status, 
               o.payment_method, o.payment_status, o.payment_id, o.table_number,
               TO_CHAR(o.created_at, 'DD/MM/YYYY HH12:MI AM') as order_date,
               TO_CHAR(o.updated_at, 'DD/MM/YYYY HH12:MI AM') as last_updated
        FROM orders o
        JOIN users u ON u.id = o.user_id
        ORDER BY o.oid DESC
    """)

    orders = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("admin_orders.html", orders=orders)

@app.route('/place_order', methods=['GET', 'POST'])
def place_order():
    if 'user_id' not in session:
        flash("Login required!", "error")
        return redirect('/login')

    if request.method == 'GET':
        cart = session.get('cart', {})
        if not cart:
            flash("Cart is empty!", "warning")
            return redirect('/cart')
        return render_template('payment_method.html')
    
    if request.method == 'POST':
        payment_method = request.form.get('payment_method')
        
        if not payment_method:
            flash("Please select a payment method!", "warning")
            return redirect('/place_order')
        
        cart = session.get('cart', {})
        if not cart:
            flash("Cart is empty!", "warning")
            return redirect('/cart')

        conn = get_db_connection()
        cur = conn.cursor()
        
        # Get user's table number
        cur.execute("SELECT table_number FROM users WHERE id=%s", (session['user_id'],))
        user_table = cur.fetchone()
        table_number = user_table[0] if user_table and user_table[0] else None

        try:
            total = 0
            for mid, qty in cart.items():
                cur.execute("SELECT price FROM menu WHERE mid=%s", (mid,))
                result = cur.fetchone()
                if result:
                    price = result[0]
                    total += price * int(qty)

            order_status = "Pending Payment" if payment_method == "Online" else "Pending"
            
            # Include created_at (automatically set by DEFAULT CURRENT_TIMESTAMP)
            cur.execute(
                """
                INSERT INTO orders (user_id, total_price, status, payment_method, payment_status, table_number)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING oid
                """,
                (session['user_id'], total, order_status, payment_method, "Pending", table_number)
            )
            
            order_id = cur.fetchone()[0]

            for mid, qty in cart.items():
                cur.execute("SELECT price FROM menu WHERE mid=%s", (mid,))
                price = cur.fetchone()[0]
                cur.execute("""
                    INSERT INTO order_items (order_id, menu_id, qty, price)
                    VALUES (%s, %s, %s, %s)
                """, (order_id, mid, int(qty), price))

            conn.commit()

            if payment_method == 'Cash':
                session.pop('cart', None)
                flash("Order placed successfully! Please pay ₹{} on delivery.".format(total))
                return redirect('/my_orders')
            
            elif payment_method == 'Online':
                razorpay_order = razorpay_client.order.create({
                    'amount': int(total * 100),
                    'currency': 'INR',
                    'payment_capture': '1'
                })
                
                cur.execute("UPDATE orders SET razorpay_order_id=%s WHERE oid=%s", 
                           (razorpay_order['id'], order_id))
                conn.commit()
                
                session['pending_order_id'] = order_id
                
                return render_template('online_payment.html', 
                                     order_id=order_id,
                                     total=total,
                                     razorpay_order_id=razorpay_order['id'],
                                     razorpay_key_id=RAZORPAY_KEY_ID)

        except Exception as e:
            conn.rollback()
            flash(f"Something went wrong while placing order: {e}")
            print("Error:", e)
            return redirect('/cart')
        finally:
            cur.close()
            conn.close()

@app.route('/payment_success', methods=['POST'])
def payment_success():
    if 'user_id' not in session:
        return redirect('/login')
    
    payment_id = request.form.get('razorpay_payment_id')
    razorpay_order_id = request.form.get('razorpay_order_id')
    signature = request.form.get('razorpay_signature')
    
    generated_signature = hmac.new(
        key=RAZORPAY_KEY_SECRET.encode('utf-8'),
        msg=f"{razorpay_order_id}|{payment_id}".encode('utf-8'),
        digestmod=hashlib.sha256
    ).hexdigest()
    
    if generated_signature == signature:
        conn = get_db_connection()
        cur = conn.cursor()
        
        try:
            payment_details = razorpay_client.payment.fetch(payment_id)
            
            if payment_details.get('status') == 'failed':
                error_code = payment_details.get('error_code')
                error_description = payment_details.get('error_description')
                error_message = get_user_friendly_error(error_code, error_description)
                
                cur.execute("""
                    UPDATE orders 
                    SET payment_status='Failed', 
                        payment_id=%s,
                        status='Payment Failed',
                        payment_error=%s
                    WHERE razorpay_order_id=%s
                """, (payment_id, error_message, razorpay_order_id))
                conn.commit()
                
                flash(f"Payment failed: {error_message}")
                return redirect('/my_orders')
            
            cur.execute("""
                UPDATE orders 
                SET payment_status='Completed', 
                    payment_id=%s,
                    status='Pending' 
                WHERE razorpay_order_id=%s
            """, (payment_id, razorpay_order_id))
            
            if cur.rowcount > 0:
                flash("Payment successful! Your order is now pending admin approval.", "success")
            else:
                flash("Payment successful but order not found. Please contact support.", "success")
                
            conn.commit()
                
        except Exception as e:
            flash("Error verifying payment. Please contact support.", "error")
            print("Payment verification error:", e)
        finally:
            cur.close()
            conn.close()
        
        session.pop('cart', None)
        session.pop('pending_order_id', None)
    else:
        flash("Payment verification failed! Please contact support.", "error")
    
    return redirect('/my_orders')

def get_user_friendly_error(error_code, error_description):
    error_messages = {
        'U69': 'Payment request expired. Please try again within the time limit.',
        'U28': 'Your bank is currently unavailable. Please try again after some time.',
        'U30': 'Debit failed due to bank issue. Please check with your bank.',
        'Z9': 'Insufficient funds in your account.',
        'Z7': 'Too many transactions in a short time. Please try after some time.',
        'Z8': 'Daily transaction limit exceeded for your account.',
        'U11': 'Invalid UPI ID. Please check and enter correct UPI ID.',
        'U13': 'UPI ID not registered with any bank.',
        'U19': 'Your bank is temporarily down. Please try again.',
        'U36': 'Transaction declined by your bank.',
        'U40': 'UPI PIN attempts exceeded. Please try after some time.',
        'U41': 'Transaction limit exceeded for this UPI ID.',
        'U42': 'Invalid UPI PIN entered.',
        'U43': 'UPI PIN blocked. Please reset your UPI PIN.',
        'U50': 'Technical error at your bank. Please try again.',
        'U51': 'Payment cancelled by you.',
        'U52': 'Your bank is undergoing maintenance. Please try later.',
        'U53': 'Transaction timeout. Please try again.',
        'U54': 'Duplicate transaction detected.',
        'U55': 'UPI service unavailable. Please try later.',
        'U56': 'Invalid request. Please try again.',
        'U57': 'Your bank does not support this transaction.',
        'U58': 'Transaction failed. Please check with your bank.',
        'U59': 'UPI PIN set up required. Please set UPI PIN in your app.',
        'U60': 'Transaction amount exceeds daily limit.',
        'U61': 'Invalid beneficiary. Please check UPI ID.',
        'U62': 'Beneficiary bank unavailable.',
        'U63': 'Invalid transaction. Please try again.',
        'U64': 'Transaction declined. Please check with your bank.',
        'U65': 'Your PSP app is not responding.',
        'U66': 'UPI app not installed on your device.',
        'U67': 'Invalid virtual address format.',
        'U68': 'UPI transaction blocked by your bank.',
        'U70': 'Technical error. Please try again.',
        'U71': 'Payment request rejected by your bank.',
        'U72': 'Invalid amount. Please check transaction amount.',
        'U73': 'Your account is temporarily blocked.',
        'U74': 'Transaction not allowed on this account.',
        'U75': 'Invalid OTP. Please try again.',
        'U76': 'OTP expired. Please request new OTP.',
        'U77': 'Maximum retry attempts exceeded.',
        'U78': 'Transaction processing. Please check status after some time.',
        'U79': 'Payment already processed.',
        'U80': 'Transaction failed due to technical error.',
        'U81': 'Invalid request parameters.',
        'U82': 'Your PSP app version is outdated. Please update.',
        'U83': 'Device binding failed. Please register your device.',
        'U84': 'Invalid mobile number registered with UPI.',
        'U85': 'UPI PIN last attempt. One more wrong attempt will block your PIN.',
        'U86': 'Transaction declined by NPCI.',
        'U87': 'Risk transaction declined.',
        'U88': 'Suspicious transaction detected. Please contact your bank.',
        'U89': 'Transaction blocked due to security reasons.',
        'U90': 'Invalid credentials. Please check and try again.',
    }
    
    return error_messages.get(error_code, error_description or 'Unknown error occurred')

@app.route('/payment_failed')
def payment_failed():
    if 'user_id' not in session:
        return redirect('/login')
    
    order_id = session.get('pending_order_id')
    
    if order_id:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("""
            UPDATE orders 
            SET status='Payment Failed' 
            WHERE oid=%s AND status='Pending Payment'
        """, (order_id,))
        
        conn.commit()
        cur.close()
        conn.close()
        
        session.pop('pending_order_id', None)
        flash("Payment was cancelled or failed. You can try again from My Orders.", "success")
    else:
        flash("No pending payment found.", "info")
    
    return redirect('/my_orders')

@app.route('/retry_payment/<int:order_id>')
def retry_payment(order_id):
    if 'user_id' not in session:
        return redirect('/login')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT total_price, status, payment_status 
        FROM orders 
        WHERE oid=%s AND user_id=%s
    """, (order_id, session['user_id']))
    
    order = cur.fetchone()
    
    if not order:
        flash("Order not found!", "error")
        return redirect('/my_orders')
    
    total = order[0]
    
    try:
        razorpay_order = razorpay_client.order.create({
            'amount': int(total * 100),
            'currency': 'INR',
            'payment_capture': '1',
            'notes': {
                'order_id': order_id,
                'retry_count': '1'
            }
        })
        
        cur.execute("""
            UPDATE orders 
            SET razorpay_order_id=%s, status='Pending Payment' 
            WHERE oid=%s
        """, (razorpay_order['id'], order_id))
        
        conn.commit()
        
        session['pending_order_id'] = order_id
        
        return render_template('online_payment.html', 
                             order_id=order_id,
                             total=total,
                             razorpay_order_id=razorpay_order['id'],
                             razorpay_key_id=RAZORPAY_KEY_ID)
    except Exception as e:
        flash("Error creating payment. Please try again.", "error")
        print("Retry payment error:", e)
        return redirect('/my_orders')
    finally:
        cur.close()
        conn.close()

@app.route('/update_payment_status/<int:order_id>')
def update_payment_status(order_id):
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute("UPDATE orders SET payment_status='Completed' WHERE oid=%s", (order_id,))
    conn.commit()
    
    cur.close()
    conn.close()
    
    flash("Payment status updated!", "success")
    return redirect('/admin/orders')

@app.route('/admin/update_order_status/<int:oid>/<string:action>')
def update_order_status(oid, action):
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')

    conn = get_db_connection()
    cur = conn.cursor()

    if action == 'approve':
        cur.execute("UPDATE orders SET status='Approved' WHERE oid=%s", (oid,))
        flash("Order approved successfully!", "success")
    elif action == 'complete':
        cur.execute("UPDATE orders SET status='Completed' WHERE oid=%s", (oid,))
        flash("Order marked as completed!", "success")
    elif action == 'reject':
        cur.execute("UPDATE orders SET status='Rejected' WHERE oid=%s", (oid,))
        flash("Order rejected!", "info")

    conn.commit()
    cur.close()
    conn.close()

    return redirect('/admin/orders')

@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out successfully", "success")
    return redirect('/')


@app.route('/health')
def health():
    return {"status": "healthy"}, 200

# ================= TABLE MANAGEMENT =================
@app.route('/admin/tables')
def admin_tables():
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get all users with their table numbers
    cur.execute("""
        SELECT id, username, email, role, table_number, created_at
        FROM users 
        ORDER BY 
            CASE WHEN table_number IS NULL THEN 1 ELSE 0 END,
            table_number,
            id DESC
    """)
    
    users = cur.fetchall()
    
    # Get occupied tables (users with table numbers)
    cur.execute("""
        SELECT table_number, COUNT(*) 
        FROM users 
        WHERE table_number IS NOT NULL 
        GROUP BY table_number
        ORDER BY table_number
    """)
    occupied_tables = cur.fetchall()
    
    # Get available tables (1-20 that are not assigned)
    occupied_nums = [str(t[0]) for t in occupied_tables]
    available_tables = [str(i) for i in range(1, 21) if str(i) not in occupied_nums]
    
    cur.close()
    conn.close()
    
    return render_template("admin_tables.html", 
                         users=users, 
                         occupied_tables=occupied_tables,
                         available_tables=available_tables)

@app.route('/admin/assign_table/<int:user_id>', methods=['POST'])
def assign_table(user_id):
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    table_number = request.form.get('table_number')
    
    if not table_number:
        flash("Please select a table number!", "warning")
        return redirect('/admin/tables')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if table is already assigned
    cur.execute("SELECT id FROM users WHERE table_number=%s AND id!=%s", 
                (table_number, user_id))
    if cur.fetchone():
        flash(f"Table {table_number} is already assigned to another user!", "error")
        cur.close()
        conn.close()
        return redirect('/admin/tables')
    
    # Assign table to user
    cur.execute("UPDATE users SET table_number=%s WHERE id=%s", 
                (table_number, user_id))
    conn.commit()
    
    cur.close()
    conn.close()
    
    flash(f"Table {table_number} assigned successfully!", "success")
    return redirect('/admin/tables')

@app.route('/admin/remove_table/<int:user_id>')
def remove_table(user_id):
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get table number before removing
    cur.execute("SELECT table_number FROM users WHERE id=%s", (user_id,))
    user = cur.fetchone()
    
    if user and user[0]:
        table_num = user[0]
        cur.execute("UPDATE users SET table_number=NULL WHERE id=%s", (user_id,))
        conn.commit()
        flash(f"Table {table_num} removed from user!", "success")
    
    cur.close()
    conn.close()
    
    return redirect('/admin/tables')

@app.route('/admin/tables/occupancy')
def table_occupancy():
    if session.get('role') != 'admin':
        flash("Admin access required!", "error")
        return redirect('/')
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get current table occupancy with user details
    cur.execute("""
        SELECT u.table_number, u.username, u.email, 
               COUNT(o.oid) as order_count,
               MAX(o.created_at) as last_order
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.id AND o.status != 'Cancelled'
        WHERE u.table_number IS NOT NULL
        GROUP BY u.table_number, u.username, u.email
        ORDER BY CAST(u.table_number AS INTEGER)
    """)
    
    occupancy = cur.fetchall()
    
    cur.close()
    conn.close()
    
    return render_template("table_occupancy.html", occupancy=occupancy)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # Only enable debug in development
    debug_mode = os.environ.get('FLASK_ENV') != 'production'
    app.run(host='0.0.0.0', port=port, debug=debug_mode)