# -*- coding: utf-8 -*-

from datetime import datetime, date
from urlparse import urlparse, parse_qs
from socket import gaierror
import os
import requests
from apiclient.discovery import build
from apiclient.errors import HttpError
from werkzeug import secure_filename
from flask import render_template, flash, escape, request, jsonify, Response
from coaster.gfm import markdown
from coaster.views import load_model, load_models
from baseframe import cache
from baseframe.forms import render_redirect, render_form, render_delete_sqla
from hgtv import app
from hgtv.views.login import lastuser
from hgtv.forms import PlaylistForm, PlaylistImportForm
from hgtv.models import db, Channel, Playlist, Video, PlaylistRedirect
from hgtv.views.video import DataProcessingError
from hgtv.uploads import thumbnails, return_werkzeug_filestorage, UploadNotAllowed


#helpers
def process_playlist(playlist, playlist_url):
    """
    Get metadata for the playlist from the corresponding site
    """
    # Parse the playlist url
    if playlist_url:
        parsed = urlparse(escape(playlist_url))
        # Check video source and get corresponding data
        if parsed.netloc in ['youtube.com', 'www.youtube.com']:
            try:
                stream_playlist = playlist.channel.playlist_for_stream(create=True)
                # first two character of playlist id says what type of playlist, ignore them
                playlist_id = parse_qs(parsed.query)['list'][0][2:]
                youtube = build('youtube', 'v3', developerKey=app.config['YOUTUBE_API_KEY'])
                playlistitems_list_request = youtube.playlistItems().list(
                    playlistId=playlist_id,
                    part='snippet',
                    maxResults=50
                )
                playlist_info_request = youtube.playlists().list(
                    id=playlist_id,
                    part='snippet'
                )
                if playlist_info_request:
                    playlist_infos = playlist_info_request.execute()
                    for playlist_info in playlist_infos['items']:
                        playlist.title = playlist.title or playlist_info['snippet']['title']
                        if playlist_info['snippet']['description']:
                            playlist.description = playlist_info['snippet']['description']
                while playlistitems_list_request:
                    playlistitems_list_response = playlistitems_list_request.execute()
                    for playlist_item in playlistitems_list_response['items']:
                        with db.session.no_autoflush:
                            video = Video.query.filter_by(video_source=u'youtube', channel=playlist.channel, video_sourceid=playlist_item['snippet']['resourceId']['videoId']).first()
                        if video:
                            if video not in stream_playlist.videos:
                                stream_playlist.videos.append(video)
                            if video not in playlist.videos:
                                playlist.videos.append(video)
                        else:
                            video = Video(playlist=playlist if playlist is not None else stream_playlist)
                            video.title = playlist_item['snippet']['title']
                            video.video_url = 'https://www.youtube.com/watch?v='+playlist_item['snippet']['resourceId']['videoId']
                            if playlist_item['snippet']['description']:
                                video.description = markdown(playlist_item['snippet']['description'])
                            for thumbnail in playlist_item['snippet']['thumbnails']['medium']:
                                thumbnail_url_request = requests.get(playlist_item['snippet']['thumbnails']['medium']['url'])
                                filestorage = return_werkzeug_filestorage(thumbnail_url_request,
                                    filename=secure_filename(playlist_item['snippet']['title']) or 'name-missing')
                                video.thumbnail_path = thumbnails.save(filestorage)
                            video.video_sourceid = playlist_item['snippet']['resourceId']['videoId']
                            video.video_source = u'youtube'
                            video.make_name()
                            playlist.videos.append(video)
                            with db.session.no_autoflush:
                                if video not in stream_playlist.videos:
                                    stream_playlist.videos.append(video)
                    playlistitems_list_request = youtube.playlistItems().list_next(
                        playlistitems_list_request, playlistitems_list_response)
            except requests.ConnectionError:
                raise DataProcessingError("Unable to establish connection")
            except gaierror:
                raise DataProcessingError("Unable to resolve the hostname")
            except KeyError:
                raise DataProcessingError("Supplied youtube URL doesn't contain video information")
            except HttpError:
                raise DataProcessingError("HTTPError while parsing YouTube playlist")
        else:
            raise ValueError("Unsupported video site")
    else:
        raise ValueError("Video URL is missing")


def remove_banner_ad(filename):
    try:
        os.remove(os.path.join(app.static_folder, 'thumbnails', filename))
    except OSError:
        pass


@app.route('/<channel>/new', methods=['GET', 'POST'])
@lastuser.requires_login
@load_model(Channel, {'name': 'channel'}, 'channel', permission='new-playlist')
def playlist_new(channel):
    # Make a new playlist
    form = PlaylistForm()
    if request.method == 'GET':
        form.published_date.data = date.today()
    form.channel = channel
    if form.validate_on_submit():
        playlist = Playlist(channel=channel)
        form.populate_obj(playlist)
        if not playlist.name:
            playlist.make_name()
        db.session.add(playlist)
        db.session.commit()
        flash(u"Created playlist '%s'." % playlist.title, 'success')
        return render_redirect(playlist.url_for(), code=303)
    return render_form(form=form, title="New Playlist", submit=u"Create",
        cancel_url=channel.url_for(), ajax=True)


@app.route('/<channel>/<playlist>/edit', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    permission='edit')
def playlist_edit(channel, playlist):
    form = PlaylistForm(obj=playlist)
    form.channel = channel
    if not playlist.banner_ad_filename:
        del form.delete_banner_ad
    message = None
    old_playlist_banner_ad_filename = playlist.banner_ad_filename
    old_playlist_name = playlist.name
    try:
        if form.validate_on_submit():
            form.populate_obj(playlist)
            if not playlist.name:
                playlist.make_name()
            playlist.banner_ad = playlist.banner_image
            if old_playlist_name != playlist.name:
                redirect_to = PlaylistRedirect.query.filter_by(name=old_playlist_name, channel=channel).first()
                if redirect_to:
                    redirect_to.playlist = playlist
                else:
                    redirect_to = PlaylistRedirect(name=old_playlist_name, channel=channel, playlist=playlist)
                    db.session.add(redirect_to)
            if playlist.banner_ad:
                if playlist.banner_ad_filename != old_playlist_banner_ad_filename:
                    remove_banner_ad(old_playlist_banner_ad_filename)
                flash(u"Added new banner ad", u"success")
                playlist.banner_ad_filename = thumbnails.save(return_werkzeug_filestorage(playlist.banner_ad, playlist.title))
                message = True
            if form.delete_banner_ad and form.delete_banner_ad.data:
                flash(u"Removed banner ad", u"success")
                message = True
                db.session.add(playlist)
                remove_banner_ad(playlist.banner_ad_filename)
                playlist.banner_ad_filename = None
                playlist.banner_ad_url = ""
            db.session.commit()
            if not message:
                flash(u"Edited playlist '%s'" % playlist.title, 'success')
            return render_redirect(playlist.url_for(), code=303)
    except UploadNotAllowed, e:
        flash(e.message, u'error')
    return render_form(form=form, title="Edit Playlist", submit=u"Save",
        cancel_url=playlist.url_for(), ajax=False)


@app.route('/<channel>/<playlist>/delete', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    permission='delete'
    )
def playlist_delete(channel, playlist):
    return render_delete_sqla(playlist, db, title=u"Confirm delete",
        message=u"Delete playlist '%s'? This cannot be undone." % playlist.title,
        success=u"You have deleted playlist '%s'." % playlist.title,
        next=channel.url_for())


@app.route('/<channel>/<playlist>')
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    ((Playlist, PlaylistRedirect), {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    permission='view')
def playlist_view(channel, playlist):
    return render_template('playlist.html.jinja2', channel=channel, playlist=playlist)


@app.route('/<channel>/<playlist>/feed')
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    permission='view')
def playlist_feed(channel, playlist):
    videos = list(playlist.videos)[::-1]
    return Response(render_template('feed.xml',
            channel=channel,
            playlist=playlist,
            videos=videos,
            feed_url=playlist.url_for(_external=True),
            updated=max([v.updated_at for v in playlist.videos] or [datetime.utcnow()]).isoformat() + 'Z'),
        content_type='application/atom+xml; charset=utf-8')


@app.route('/<channel>/import', methods=['GET', 'POST'])
@lastuser.requires_login
@load_model(Channel, {'name': 'channel'}, 'channel', permission='new-playlist')
def playlist_import(channel):
    # Import playlist
    form = PlaylistImportForm()
    form.channel = channel
    if form.validate_on_submit():
        playlist = Playlist(channel=channel)
        form.populate_obj(playlist)
        try:
            process_playlist(playlist, playlist_url=form.playlist_url.data)
            if not playlist.name:
                playlist.make_name()
            db.session.add(playlist)
            db.session.commit()
            cache.delete('data/featured-channels')
        except (DataProcessingError, ValueError) as e:
            flash(e.message, category="error")
            return render_form(form=form, title="Import Playlist", submit=u"Import",
                cancel_url=channel.url_for(), ajax=False)
        flash(u"Imported playlist '%s'." % playlist.title, 'success')
        return render_redirect(playlist.url_for(), code=303)
    return render_form(form=form, title="Import Playlist", submit=u"Import",
        cancel_url=channel.url_for(), ajax=False)


@app.route('/<channel>/<playlist>/extend', methods=['GET', 'POST'])
@lastuser.requires_login
@load_models(
    (Channel, {'name': 'channel'}, 'channel'),
    (Playlist, {'name': 'playlist', 'channel': 'channel'}, 'playlist'),
    permission='extend')
def playlist_extend(channel, playlist):
    form = PlaylistImportForm()
    form.channel = channel
    html = render_template('playlist-extend.html.jinja2', form=form, channel=channel, playlist=playlist)
    if request.is_xhr:
        if form.validate_on_submit():
            playlist_url = escape(form.playlist_url.data)
            initial_count = len(playlist.videos)
            try:
                process_playlist(playlist_url=playlist_url, playlist=playlist)
            except:
                return jsonify({'message_type': "server-error",
                    'message': 'Oops, something went wrong, please try later'})
            additions = (len(playlist.videos) - initial_count)
            if additions:
                db.session.commit()
                cache.delete('data/featured-channels')
                flash(u"Added '%d' videos" % (len(playlist.videos) - initial_count), 'success')
                return jsonify({'message_type': "success", 'action': 'redirect', 'url': playlist.url_for()})
            return jsonify({'message_type': "success", 'action': 'noop', 'message': 'Already upto date'})
        if form.errors:
            html = render_template('playlist-extend.html.jinja2', form=form, channel=channel, playlist=playlist)
            return jsonify({'message_type': "error", 'action': 'append',
                'html': html})
        return jsonify({'action': 'modal-window', 'message_type': 'success', 'html': html})
    return html
