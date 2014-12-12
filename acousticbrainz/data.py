import psycopg2
from flask import current_app
from acousticbrainz.utils import sanity_check_json, clean_metadata, interpret_high_level
from werkzeug.exceptions import BadRequest, ServiceUnavailable, InternalServerError, NotFound
from hashlib import sha256
import json
import time


def submit_low_level(mbid, data):
    """Function for submitting low-level data.

    Args:
        mbid: MusicBrainz ID of the track that corresponds to the data that is
            being submitted.

    """
    mbid = str(mbid)
    data = clean_metadata(data)

    try:
        # If the user submitted a trackid key, rewrite to recording_id
        if "musicbrainz_trackid" in data['metadata']['tags']:
            val = data['metadata']['tags']["musicbrainz_trackid"]
            del data['metadata']['tags']["musicbrainz_trackid"]
            data['metadata']['tags']["musicbrainz_recordingid"] = val

        if data['metadata']['audio_properties']['lossless']:
            data['metadata']['audio_properties']['lossless'] = True
        else:
            data['metadata']['audio_properties']['lossless'] = False

    except KeyError:
        pass

    # sanity check the incoming data
    err = sanity_check_json(data)
    if err:
        raise BadRequest(err)

    # Ensure the MBID form the URL matches the recording_id from the POST data
    if data['metadata']['tags']["musicbrainz_recordingid"][0].lower() != mbid.lower():
        raise BadRequest("The musicbrainz_trackid/musicbrainz_recordingid in"
                         "the submitted data does not match the MBID that is"
                         "part of this resource URL.")

    # The data looks good, lets see about saving it
    is_lossless_submit = data['metadata']['audio_properties']['lossless']
    build_sha1 = data['metadata']['version']['essentia_build_sha']
    data_json = json.dumps(data, sort_keys=True, separators=(',', ':'))
    data_sha256 = sha256(data_json).hexdigest()

    conn = psycopg2.connect(current_app.config['PG_CONNECT'])
    cur = conn.cursor()
    try:
        # Checking to see if we already have this data
        cur.execute("SELECT data_sha256 FROM lowlevel WHERE mbid = %s", (mbid, ))

        # if we don't have this data already, add it
        sha_values = [v[0] for v in cur.fetchall()]

        if data_sha256 not in sha_values:
            current_app.logger.info("Saved %s" % mbid)
            cur.execute("INSERT INTO lowlevel (mbid, build_sha1, data_sha256, lossless, data)"
                        "VALUES (%s, %s, %s, %s, %s)",
                        (mbid, build_sha1, data_sha256, is_lossless_submit, data_json))
            conn.commit()
            return ""

        current_app.logger.info("Already have %s" % data_sha256)

    except psycopg2.ProgrammingError, e:
        raise BadRequest(str(e))
    except psycopg2.IntegrityError, e:
        raise BadRequest(str(e))
    except psycopg2.OperationalError, e:
        raise ServiceUnavailable(str(e))


def load_low_level(mbid):
    """Load low level data for a given MBID."""
    conn = psycopg2.connect(current_app.config['PG_CONNECT'])
    cur = conn.cursor()
    try:
        cur.execute("SELECT data::text FROM lowlevel WHERE mbid = %s", (str(mbid), ))
        if not cur.rowcount:
            raise NotFound

        row = cur.fetchone()
        return row[0]

    except psycopg2.IntegrityError, e:
        raise BadRequest(str(e))
    except psycopg2.OperationalError, e:
        raise ServiceUnavailable(str(e))

    return InternalServerError("whoops, looks like a cock-up on our part!")


def load_high_level(mbid):
    """Load high level data for a given MBID."""
    conn = psycopg2.connect(current_app.config['PG_CONNECT'])
    cur = conn.cursor()
    try:
        cur.execute("""SELECT hlj.data::text
                         FROM highlevel hl
                         JOIN highlevel_json hlj
                           ON hl.data = hlj.id
                        WHERE mbid = %s""", (str(mbid), ))
        if not cur.rowcount:
            raise NotFound

        row = cur.fetchone()
        return row[0]

    except psycopg2.IntegrityError, e:
        raise BadRequest(str(e))
    except psycopg2.OperationalError, e:
        raise ServiceUnavailable(str(e))

    return InternalServerError("Bummer, dude.")


def get_summary_data(mbid):
    mbid = str(mbid)
    conn = psycopg2.connect(current_app.config['PG_CONNECT'])
    cur = conn.cursor()
    try:
        cur.execute("SELECT data FROM lowlevel WHERE mbid = %s", (mbid, ))
        if not cur.rowcount:
            raise NotFound("We do not have data for this track. Please upload it!")

        row = cur.fetchone()
        lowlevel = row[0]
        if 'artist' not in lowlevel['metadata']['tags']:
            lowlevel['metadata']['tags']['artist'] = ["[unknown]"]
        if 'release' not in lowlevel['metadata']['tags']:
            lowlevel['metadata']['tags']['release'] = ["[unknown]"]
        if 'title' not in lowlevel['metadata']['tags']:
            lowlevel['metadata']['tags']['title'] = ["[unknown]"]

        # Format track length readably (mm:ss)
        lowlevel['metadata']['audio_properties']['length_formatted'] = \
            time.strftime("%M:%S", time.gmtime(lowlevel['metadata']['audio_properties']['length']))

        cur.execute("SELECT hlj.data "
                    "FROM highlevel hl, highlevel_json hlj "
                    "WHERE hl.data = hlj.id "
                    "AND hl.mbid = %s", (mbid, ))
        genres = None
        moods = None
        other = None
        highlevel = None
        if cur.rowcount:
            row = cur.fetchone()
            highlevel = row[0]
            try:
                genres, moods, other = interpret_high_level(highlevel)
            except KeyError:
                pass

        return lowlevel, highlevel, genres, moods, other


    except psycopg2.IntegrityError, e:
        raise BadRequest(str(e))
    except psycopg2.OperationalError, e:
        raise ServiceUnavailable(str(e))

    return InternalServerError("whoops!")
