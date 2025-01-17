from app import app
from flask import render_template, request, flash
from flask import Flask, jsonify, make_response, redirect, url_for
from StravaEffort import StravaActivity
from StravaUser import StravaUser
from StravaModel import StravaModel
from StravaAPI import StravaAPI
from StravaDB import StravaDB
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.cross_validation import cross_val_score, train_test_split
from SignalProc import smooth
import numpy as np
import pandas as pd
import requests
import pymongo
import json
from datetime import datetime
from bson.binary import Binary
import pickle
import os.path
from werkzeug import secure_filename


# CLIENT = pymongo.MongoClient(
#     """mongodb://sethah:abc123@ds049161.mongolab.com:49161/strava"""
#     )
# MONGODB = CLIENT.strava
DB = StravaDB()

app.config['UPLOAD_FOLDER'] = 'app/uploads/'  # this breaks on pythonanywhere


@app.route('/', methods=['GET', 'POST'])
@app.route('/index')
def index():
    """
    Main page to display current users
    """
    DB = StravaDB()
    # get all users
    q = """ SELECT
                id, firstname, lastname, city, state
            FROM athletes;
        """
    athletes = DB.execute(q)

    return render_template('home.html', athletes=athletes)


@app.route('/token_exchange', methods=['GET', 'POST'])
def token_exchange():
    """
    This page receives an auth token for a user from Strava's API
    and then stores this token and the user in the database.
    """
    code = request.args.get('code', None)
    api = StravaAPI()
    data = api.exchange_token(code)
    ath = data['athlete']
    ath['token'] = data['access_token']
    DB = StravaDB()
    d = {'id': ath['id'],
         'firstname': ath['firstname'],
         'lastname': ath['lastname'],
         'sex': ath['sex'],
         'city': ath['city'],
         'state': ath['state'],
         'country': ath['country'],
         'access_key': ath['token']}
    DB.insert_values('athletes', d)
    DB.conn.commit()

    return redirect(url_for('index'))


@app.route('/fit', methods=['POST'])
def fit():
    """
    Fit a model to a user's data and store the pickled model
    """
    # train a model on some of the data
    uid = int(request.form.get('userid', None))

    user = StravaUser(uid, get_streams=True, get_routes=False)

    indices = np.arange(len(user.activities))
    train_indices = np.random.choice(indices,
                                     size=int(len(user.activities)*0.75),
                                     replace=False)
    test_indices = np.setdiff1d(indices, train_indices)

    df = user.make_df(train_indices)
    y = df['velocity'].values
    cols = df.columns
    X = df[cols[np.where(cols != 'velocity')]].values
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3)

    model = GradientBoostingRegressor(n_estimators=100, max_depth=3)
    # model = RandomForestRegressor(max_depth=20, min_samples_split=50)
    print 'Fitting model.......'
    model.fit(X_train, y_train)
    print 'Model fit!'

    print 'Loading pickle'
    if os.path.isfile('model_%s.pkl' % user.userid):
        d = pickle.load(open('model_%s.pkl' % user.userid, 'rb'))
    else:
        d = {}

    print 'Dumping pickle'
    d[user.userid] = {'date': datetime.now(), 'model': model}
    pickle.dump(d, open('model_%s.pkl' % user.userid, 'wb'))
    print 'Pickle dumped'

    return str(len(user.activities))


@app.route('/upload', methods=['POST'])
def upload_gpx():
    """
    Receive an uploaded gpx file, parse it, and store the path
    as a route in the database.
    """
    uid = int(request.form.get('athlete_id', 0))
    ride_name = request.form.get('ride_title', 'New Route')

    if ride_name.strip() == '':
        ride_name = 'New Route'

    f = request.files['file']

    if f:
        # Make the filename safe, remove unsupported chars
        print f.filename
        filename = secure_filename(f.filename)
        fpath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        f.save(os.path.abspath(fpath))
        DB = StravaDB()
        DB.create_route(fpath, uid, ride_name)
    return redirect(url_for('rides', userid=uid))


@app.route('/delete/route', methods=['POST'])
def delete_route():
    """Delete an uploaded route from the database"""

    route_id = int(request.form.get('route_id', 0))
    athlete_id = int(request.form.get('athlete_id', 0))

    DB = StravaDB()
    q = """ DELETE FROM streams
            WHERE activity_id = %s
            AND athlete_id = %s
        """ % (route_id, athlete_id)
    DB.cur.execute(q)

    q = """ DELETE FROM routes
            WHERE id = %s
            AND athlete_id = %s
        """ % (route_id, athlete_id)
    DB.cur.execute(q)

    DB.conn.commit()

    return ''


@app.route('/get-data', methods=['POST'])
def get_data():
    """Retrieve a user's data from the Strava API"""

    uid = request.form.get('userid', None)
    u = StravaUser(int(uid), get_routes=False)
    u.get_activities()

    return str(len(u.activities))


@app.route('/check', methods=['POST'])
def check():
    """Check if a user has data and/or has a model fit to their data"""

    uid = request.form.get('userid', None)
    print 'checkid', uid

    # if the user has no activities, get them from Strava
    DB = StravaDB()
    q = """SELECT COUNT(*) FROM activities WHERE athlete_id = %s""" % uid
    DB.cur.execute(q)
    num_activities = DB.cur.fetchone()[0]

    print 'Number of activities: ', num_activities
    if num_activities == 0:
        return 'new'

    has_model = os.path.isfile('model_%s.pkl' % uid)
    print 'Has model', has_model

    if has_model == 0:
        return 'predict'

    return 'good'


@app.route('/rides/<userid>', methods=['GET', 'POST'])
def rides(userid):
    """
    Rides page shows a user's rides and displays predictions and a map
    for one of their rides.
    """

    print 'creating user'
    u = StravaUser(int(userid))
    activities = []
    routes = []
    for a in u.activities:
        if a.is_route:
            routes.append(a)
        else:
            activities.append(a)

    # pass a single activity with all the streams
    activity = u.activities[0]
    activity.init_streams()
    d = pickle.load(open('model_%s.pkl' % u.userid, 'rb'))
    activity.predict(d[u.userid]['model'])

    DB = StravaDB()
    # get all users
    q = """ SELECT
                id, firstname, lastname, city, state
            FROM athletes;
        """
    athletes = DB.execute(q)

    return render_template(
        'rides.html',
        athlete=u,
        activities=activities,
        routes=routes,
        activity=activity,
        athletes=athletes)


@app.route('/change', methods=['POST'])
def change():
    """
    Get an activity or route from the database and return a
    json object containing the necessary vectors.
    """

    aid = int(request.form.get('activity_id', 0))
    uid = int(request.form.get('athlete_id', 0))
    print 'Initializing activity'
    # TODO: FIX THIS AWFUL HACKY SHIT
    if aid < 10000:
        a = StravaActivity(aid, uid, get_streams=True, is_route=True)
    else:
        a = StravaActivity(aid, uid, get_streams=True)

    print 'Loading model'
    d = pickle.load(open('model_%s.pkl' % uid, 'rb'))

    print 'Predicting'
    a.predict(d[uid]['model'])
    print 'Predicted'

    actual, predicted = a.to_dict2()
    if not a.is_route:
        d, pd = truncate(actual['plot_distance'], predicted['plot_distance'])
        t, pt = truncate(actual['plot_time'], predicted['plot_time'])
        predicted['distance_diff'] = (np.array(d) - np.array(pd)).tolist()
        predicted['time_diff'] = (np.array(pt) -
                                  np.interp(np.array(d),
                                  np.array(pd), np.array(pt))).tolist()

    return jsonify({'actual': actual, 'predicted': predicted})


@app.route('/add_rider', methods=['POST'])
def add_rider():
    activity_id = int(request.form.get('activity_id', 0))
    athlete_id = int(request.form.get('athlete_id', 0))
    the_dict = load_model(athlete_id)
    if the_dict is None:
        return ''

    time_spacing = float(request.form.get('time_spacing'))
    the_rider_distance = json.loads(request.form.get('the_rider_distance'))

    new_user = StravaUser(athlete_id)
    if int(activity_id) < 10000:
        ride = StravaActivity(activity_id, new_user.userid, belongs_to='other', is_route=True, get_streams=True)
    else:
        ride = StravaActivity(activity_id, new_user.userid, belongs_to='other', get_streams=True)

    ride.predict(the_dict[new_user.userid]['model'])
    actual, predicted = ride.to_dict2(time_spacing)
    d, pd = truncate(the_rider_distance, predicted['plot_distance'])
    t, pt = truncate(the_rider_distance, predicted['plot_time'])

    predicted['distance_diff'] = (np.array(d) - np.array(pd)).tolist()
    predicted['time_diff'] = (np.array(pt) -
                              np.interp(np.array(d),
                                        np.array(pd),
                                        np.array(pt))).tolist()

    return jsonify(predicted)


def truncate(a, b, keep_dim=0):
    if keep_dim == 0:
        if len(a) < len(b):
            return a, b[:len(a)]
        else:
            return a, b + [b[-1]]*(len(a)-len(b))
    else:
        if len(b) < len(a):
            return a[:len(b)], b
        else:
            return a + [a[-1]]*(len(b)-len(a)), b


@app.route("/chart")
def chart():
    return render_template('dialog.html')


def load_model(athlete_id):
    fname = 'model_%s.pkl' % athlete_id
    print fname
    if os.path.isfile(fname):
        return pickle.load(open(fname, 'rb'))
    else:
        print 'file does not exist'
        return None


@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500


if __name__ == '__main__':
    pass
