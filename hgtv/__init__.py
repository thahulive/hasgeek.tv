# -*- coding: utf-8 -*-

# The imports in this file are order-sensitive

from pytz import timezone
from flask import Flask
from flask_migrate import Migrate
from flask_assets import Bundle
from flask_lastuser import Lastuser
from flask_lastuser.sqlalchemy import UserManager
from baseframe import baseframe, assets, Version
import coaster.app
from ._version import __version__

version = Version(__version__)
app = Flask(__name__, instance_relative_config=True)
lastuser = Lastuser()

assets['hgtv.css'][version] = 'css/app.css'
assets['presentz.js'][Version('1.2.2')] = 'js/presentz-1.2.2.js'
assets['froogaloop.js'][Version('2.0.0')] = 'js/froogaloop2.min.js'

from . import models, views, uploads
from .models import db


# Configure the app
coaster.app.init_app(app)
migrate = Migrate(app, db)
baseframe.init_app(app, requires=['baseframe-mui', 'jquery-easytabs', 'toastr', 'swfobject', 'froogaloop', 'hgtv'],
    bundle_js=Bundle(assets.require('presentz.js'), filters='jsmin', output='js/presentz.min.js'),
    theme='mui')
models.commentease.init_app(app)
lastuser.init_app(app)
lastuser.init_usermanager(UserManager(db, models.User))
app.config['tz'] = timezone(app.config['TIMEZONE'])
uploads.configure(app)
