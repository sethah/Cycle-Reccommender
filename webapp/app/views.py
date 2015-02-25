from app import app
from flask import render_template, request, flash
from flask import Flask, jsonify, make_response, redirect, url_for
from StravaEffort import StravaActivity
from StravaUser import StravaUser
from StravaModel import StravaModel
from StravaAPI import StravaAPI
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
import numpy as np
import requests
import pymongo
import json


# CLIENT = pymongo.MongoClient()
CLIENT = pymongo.MongoClient("mongodb://sethah:abc123@ds049161.mongolab.com:49161/strava")
DB = CLIENT.strava

@app.route('/', methods=['GET', 'POST'])
@app.route('/index')
def index():
    # get all users
    athletes = DB.athletes.find()[:]
    return render_template('home.html', athletes=athletes)

@app.route('/token_exchange', methods=['GET', 'POST'])
def token_exchange():
    code = request.args.get('code', None)
    api = StravaAPI()
    data = api.exchange_token(code)
    athlete_dict = data['athlete']
    athlete_dict['token'] = {'access_token': data['access_token'],
                             'token_type': data['token_type']}
    if DB.athletes.find_one({'id': athlete_dict['id']}) is None:
        DB.athletes.insert(athlete_dict)

    return redirect(url_for('index'))


@app.route('/train')
def train():
    # get the strava data if not already there

    # train a model on all of the data
    uid = 4478600
    u = StravaUser(uid, get_streams=True)
    all_rides_df = u.make_df()
    y = all_rides_df.pop('time_int')
    X = all_rides_df.values
    # model = RandomForestRegressor(max_depth=8)
    model = GradientBoostingRegressor()
    model.fit(X, y)
    m = StravaModel(model)
    for a in u.activities:
        forecast, true, pred_time = m.predict_activity(a)
        DB.activities.update(
            {'id': a.id},
            {'$set': {'streams.predicted_time.data': np.cumsum(forecast).tolist(),
                    'predicted_moving_time': pred_time}}
            )
    return render_template('train.html')

@app.route('/rides/<userid>', methods=['GET', 'POST'])
def rides(userid):

    print 'creating user'
    u = StravaUser(int(userid))

    # if the user has no activities, get them from Strava
    if len(u.activities) == 0:
        # this will take a bit
        print 'storing activities'
        u.get_activities()

    if not u.has_full_predictions():
        # analyze their data!
        print 'fitting model'
        return redirect(url_for('train'))
    
    # TODO: this is a really stupid way to do it, refactor the get_streams
    aid = 134934515
    a = DB.activities.find({'id': request.form.get('id', aid)})[0]
    a = StravaActivity(a, get_streams=True)
    a.time.raw_data -= a.time.raw_data[0]
    a.distance.raw_data -= a.distance.raw_data[0]


    return render_template(
        'poly.html',
        activity = a,
        activities = u.activities)

@app.route('/change', methods=['POST'])
def change():
    aid = int(request.form.get('id', 0))
    a = DB.activities.find_one({'id': aid})
    a = StravaActivity(a, get_streams=True)
    a.time.raw_data -= a.time.raw_data[0]
    a.distance.raw_data -= a.distance.raw_data[0]
    print a.name, a.time.raw_data[-1]
    return jsonify(a.to_dict())

@app.route("/chart")
def chart():
    return render_template('chart.html')

@app.route("/loading")
def loading():
    return render_template('chart.html')

@app.route('/sleep', methods=['POST'])
def sleep():
    import time
    time.sleep(5)

    return 'Done!'

if __name__ == '__main__':
    pass