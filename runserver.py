#!/usr/bin/env python

from hgtv import app, init_for
init_for('development')
app.run('0.0.0.0', debug=True, port=8000)
