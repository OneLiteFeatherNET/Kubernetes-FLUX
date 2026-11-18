"""Print the username that should own the imported Corvus dashboard (empty if no user exists yet).

Prefers a user with the Admin role. Runs inside the Superset image with the bootstrap environment:
    /app/.venv/bin/python /app/corvus-bundle/owner_username.py
"""

from superset.app import create_app

app = create_app()
with app.app_context():
    from flask_appbuilder.security.sqla.models import User

    from superset import db

    users = db.session.query(User).order_by(User.id).all()
    admins = [user for user in users if any(role.name == "Admin" for role in user.roles)]
    chosen = (admins or users)[:1]
    print(chosen[0].username if chosen else "")
