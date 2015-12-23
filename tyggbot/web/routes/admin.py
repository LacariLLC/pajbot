import datetime
import base64
import binascii
import logging
import collections

from tyggbot.web.utils import requires_level
from tyggbot.models.filter import Filter
from tyggbot.models.command import Command, CommandData, CommandManager
from tyggbot.models.timer import Timer
from tyggbot.models.linkchecker import BlacklistedLink
from tyggbot.models.linkchecker import WhitelistedLink
from tyggbot.models.user import User
from tyggbot.models.sock import SocketClientManager
from tyggbot.models.db import DBManager

import requests
from flask import Blueprint
from flask import jsonify
from flask import make_response
from flask import request
from flask import redirect
from flask import render_template
from flask import session
from flask import abort
from flask.ext.scrypt import generate_password_hash
from flask.ext.scrypt import check_password_hash
from sqlalchemy import func
from sqlalchemy import and_

page = Blueprint('admin', __name__, url_prefix='/admin')

log = logging.getLogger(__name__)


@page.route('/')
@requires_level(500)
def home(**options):
    return render_template('admin/home.html')

@page.route('/banphrases/')
@requires_level(500)
def banphrases(**options):
    with DBManager.create_session_scope() as db_session:
        banphrases = db_session.query(Filter).filter_by(enabled=True, type='banphrase').all()
        return render_template('admin/banphrases.html',
                banphrases=banphrases)

@page.route('/links/blacklist/')
@requires_level(500)
def links_blacklist(**options):
    with DBManager.create_session_scope() as db_session:
        links = db_session.query(BlacklistedLink).filter_by().all()
        return render_template('admin/links_blacklist.html',
                links=links)

@page.route('/links/whitelist/')
@requires_level(500)
def links_whitelist(**options):
    with DBManager.create_session_scope() as db_session:
        links = db_session.query(WhitelistedLink).filter_by().all()
        return render_template('admin/links_whitelist.html',
                links=links)

@page.route('/commands/')
@requires_level(500)
def commands(**options):
    from tyggbot.models.command import CommandManager
    bot_commands = CommandManager(None).load()

    bot_commands_list = bot_commands.parse_for_web()
    custom_commands = []
    point_commands = []
    moderator_commands = []

    for command in bot_commands_list:
        if command.id is None:
            continue
        if command.level > 100 or command.mod_only:
            moderator_commands.append(command)
        elif command.cost > 0:
            point_commands.append(command)
        else:
            custom_commands.append(command)

    return render_template('admin/commands.html',
            custom_commands=sorted(custom_commands, key=lambda f: f.command),
            point_commands=sorted(point_commands, key=lambda a: (a.cost, a.command)),
            moderator_commands=sorted(moderator_commands, key=lambda c: (c.level if c.mod_only is False else 500, c.command)),
            created=session.pop('command_created_id', None),
            edited=session.pop('command_edited_id', None))

@page.route('/commands/edit/<command_id>')
@requires_level(500)
def commands_edit(command_id, **options):
    with DBManager.create_session_scope() as db_session:
        command = db_session.query(Command).filter_by(id=command_id).one_or_none()

        if command is None:
            return render_template('admin/command_404.html'), 404

        return render_template('admin/edit_command.html',
                command=command,
                user=options.get('user', None))

@page.route('/commands/create', methods=['GET', 'POST'])
@requires_level(500)
def commands_create(**options):
    session.pop('command_created_id', None)
    session.pop('command_edited_id', None)
    if request.method == 'POST':
        if 'aliases' not in request.form:
            abort(403)
        alias_str = request.form.get('aliases', '').replace('!', '').lower()
        delay_all = request.form.get('cd', Command.DEFAULT_CD_ALL)
        delay_user = request.form.get('usercd', Command.DEFAULT_CD_USER)
        level = request.form.get('level', Command.DEFAULT_LEVEL)
        cost = request.form.get('cost', 0)

        try:
            delay_all = int(delay_all)
            delay_user = int(delay_user)
            level = int(level)
            cost = int(cost)
        except ValueError:
            abort(403)

        if len(alias_str) == 0:
            abort(403)
        if delay_all < 0 or delay_all > 9999:
            abort(403)
        if delay_user < 0 or delay_user > 9999:
            abort(403)
        if level < 0 or level > 2000:
            abort(403)
        if cost < 0 or cost > 9999999:
            abort(403)

        options = {
                'delay_all': delay_all,
                'delay_user': delay_user,
                'level': level,
                'cost': cost,
                }

        valid_action_types = ['say', 'me', 'whisper', 'reply']
        action_type = request.form.get('reply', 'say').lower()
        if action_type not in valid_action_types:
            abort(403)

        response = request.form.get('response', '')
        if len(response) == 0:
            abort(403)

        action = {
                'type': action_type,
                'message': response
                }
        options['action'] = action

        command_manager = CommandManager(None)

        internal_commands = command_manager.get_internal_commands()
        db_command_aliases = []

        with DBManager.create_session_scope() as db_session:
            for command in db_session.query(Command):
                db_command_aliases.extend(command.command.split('|'))

        for alias in internal_commands:
            db_command_aliases.append(alias)

        db_command_aliases = set(db_command_aliases)

        alias_list = alias_str.split('|')

        for alias in alias_list:
            if alias in db_command_aliases:
                return render_template('admin/create_command_fail.html')

        command = Command(command=alias_str, **options)
        command.data = CommandData(command.id)
        with DBManager.create_session_scope(expire_on_commit=False) as db_session:
            db_session.add(command)
            db_session.add(command.data)
            db_session.commit()
            db_session.expunge(command)
            db_session.expunge(command.data)

        SocketClientManager.send('command.update', {'command_id': command.id})
        session['command_created_id'] = command.id
        return redirect('/admin/commands/', 303)
    else:
        return render_template('admin/create_command.html')

@page.route('/timers/')
@requires_level(500)
def timers(**options):
    with DBManager.create_session_scope() as db_session:
        return render_template('admin/timers.html',
                timers=db_session.query(Timer).all(),
                created=session.pop('timer_created_id', None),
                edited=session.pop('timer_edited_id', None))

@page.route('/timers/edit/<timer_id>')
@requires_level(500)
def timers_edit(timer_id, **options):
    with DBManager.create_session_scope() as db_session:
        timer = db_session.query(Timer).filter_by(id=timer_id).one_or_none()

        if timer is None:
            return render_template('admin/timer_404.html'), 404

        return render_template('admin/create_timer.html',
                timer=timer)

@page.route('/timers/create', methods=['GET', 'POST'])
@requires_level(500)
def timers_create(**options):
    session.pop('timer_created_id', None)
    session.pop('timer_edited_id', None)
    if request.method == 'POST':
        id = None
        try:
            if 'id' in request.form:
                id = int(request.form['id'])
            name = request.form['name'].strip()
            interval_online = int(request.form['interval_online'])
            interval_offline = int(request.form['interval_offline'])
            message_type = request.form['message_type']
            message = request.form['message'].strip()
        except (KeyError, ValueError):
            abort(403)

        if interval_online < 0 or interval_offline < 0:
            abort(403)

        if message_type not in ['say', 'me']:
            abort(403)

        if len(message) == 0:
            abort(403)

        options = {
                'name': name,
                'interval_online': interval_online,
                'interval_offline': interval_offline,
                }

        action = {
                'type': message_type,
                'message': message
                }
        options['action'] = action

        if id is None:
            timer = Timer(**options)

        with DBManager.create_session_scope(expire_on_commit=False) as db_session:
            if id is not None:
                timer = db_session.query(Timer).filter_by(id=id).one_or_none()
                if timer is None:
                    return redirect('/admin/timers/', 303)
                timer.set(**options)
            else:
                db_session.add(timer)

        SocketClientManager.send('timer.update', {'timer_id': timer.id})
        if id is None:
            session['timer_created_id'] = timer.id
        else:
            session['timer_edited_id'] = timer.id
        return redirect('/admin/timers/', 303)
    else:
        return render_template('admin/create_timer.html')

@page.route('/moderators/')
@requires_level(500)
def moderators(**options):
    with DBManager.create_session_scope() as db_session:
        moderator_users = db_session.query(User).filter(User.level > 100).order_by(User.level.desc()).all()
        userlists = collections.OrderedDict()
        userlists['Admins'] = list(filter(lambda user: user.level >= 2000, moderator_users))
        userlists['Super Moderators/Broadcaster'] = list(filter(lambda user: user.level >= 1000 and user.level < 2000, moderator_users))
        userlists['Moderators'] = list(filter(lambda user: user.level >= 500 and user.level < 1000, moderator_users))
        userlists['Notables/Helpers'] = list(filter(lambda user: user.level >= 101 and user.level < 500, moderator_users))
        return render_template('admin/moderators.html',
                userlists=userlists)
