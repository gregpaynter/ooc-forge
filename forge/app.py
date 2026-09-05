from forge.video_web import bp as video_derivative_bp
from forge.web import create_app

app = create_app()
app.register_blueprint(video_derivative_bp)
