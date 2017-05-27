# -*- coding: utf-8 -*-  

from flask import Flask, render_template
from flask.ext.socketio import SocketIO, emit
from flask.ext.socketio import join_room, leave_room
from kafka import KafkaConsumer
import json
import datetime
import random
import threading

app = Flask(__name__)
app.debug = True
app.config['SECRET_KEY'] = 'secret!'

socketio = SocketIO(app)
data = {}
user_ad_state = {}
update_chart = False
chart_node_interval = 300
lock = threading.Lock()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/c1')
def client1():
    return render_template('client1.html')

#@app.route('/metrics')
def test_message():
    global lock
    lock.acquire()
    consumer = KafkaConsumer('realtime_viz', bootstrap_servers=['192.168.112.49:6667','192.168.112.50:6667','192.168.112.52:6667'])
    global data
    global update_chart
    global chart_node_interval
    global user_ad_state
    data_current = {}
    previous_time = None
    previous_record_time = None
    signal = True
    for message in consumer:
        #print ("topic=%s key=%s value=%s" % (message.topic, message.key, message.value))
        item = json.loads(message.value)
        current_time = datetime.datetime.strptime(message.key, '%Y-%m-%d %H:%M:%S')
        # 第一次接收数据，上次处理时间赋初始值
        if not previous_time:
            previous_time = current_time
            previous_record_time = current_time
        # 上次处理时间和数据发送时间不是一天，初始化data（跨天重置）
        if previous_time.strftime('%Y-%m-%d') != current_time.strftime('%Y-%m-%d'):
            data = {}
        # 数据中日期和当前日期不一致，直接跳过
        if item['action_date'] != datetime.datetime.now().strftime('%Y-%m-%d'):
            continue
            
        # 插入历史记录数据,间隔超过几分钟的数据才处理
        if (current_time-previous_record_time).seconds >= chart_node_interval:
            if signal:
                if item['ad_id'] in data:
                    data[item['ad_id']].append([item['pv'], item['uv'], item['action_date'], item['log_time']])
                else:
                    data[item['ad_id']] = [[item['pv'], item['uv'], item['action_date'], item['log_time']]]
                signal = False
            else:
                # 同一批数据，全部积累起来
                if (current_time - previous_time).seconds < 5:
                    if item['ad_id'] in data:
                        data[item['ad_id']].append([item['pv'], item['uv'], item['action_date'], item['log_time']])
                    else:
                        data[item['ad_id']] = [[item['pv'], item['uv'], item['action_date'], item['log_time']]]        
                # 不同批数据，更新时间戳等待下次间隔
                else:
                    previous_record_time = previous_time
                    signal = True

        # 构造实时发送数据
        data_current[item['ad_id']] = [item['pv'], item['uv'], item['action_date'], item['log_time']]

        # 上次处理时间和数据发送时间间隔大于一定值就socket出去，否则继续累积同一批发送的数据
        if (current_time-previous_time).seconds > 5:
            print data_current
            print len(data.keys())
            #if update_chart: # 如果true，socket每分钟发送最新记录，否则不发送等待init事件
            for user,ad in user_ad_state.iteritems():
                data_send = realtime_data_filter(data_current, ad)
                socketio.emit('my response', data_send, room=str(user)+'update', namespace='/test')
            data_current = {}
        else:
            continue
        previous_time = current_time
    lock.release()

@socketio.on('connect', namespace='/test')
def test_connect():
    #socketio.emit('my response', {'data': 'Connected'})
    print("Client connected")

@socketio.on('disconnect', namespace='/test')
def test_disconnect():
    print('Client disconnected')

@socketio.on('init', namespace='/test')
def handle_init_event(msg):
    global data
    global user_ad_state
    #global update_chart
    print "************************"
    print "Got init event: " + str(msg)
    print "************************"
    #update_chart = False
    join_room(msg['session_id'])
    # 用户没有选择进行init时先约定随机选取n个id进行展示
    if '0' in msg['ad_id'] or 0 in msg['ad_id']:
        if len(data.keys()) < len(msg['ad_id']):
            ad_ids = [str(item) for item in range(10000, 10000+len(msg['ad_id']))]
        else:
            ad_ids = random.sample(data.keys(), len(msg['ad_id']))
    else:
        ad_ids = msg['ad_id']
    user_ad_state[msg['session_id']] = ad_ids
    #if isinstance(msg, list):
    # 删除间隔两分钟内的历史记录
    data_filtered = history_data_filter(data, ad_ids)
    print data_filtered.keys()
    # socket发送历史记录，恢复原始设置
    socketio.emit('init', data_filtered, room=msg['session_id'], namespace='/test')

@socketio.on('update', namespace='/test')
def handle_update_event(msg):
    print "************************"
    print "Got update event: " + str(msg)
    print "************************"
    #global update_chart
    # 开始每分钟socket最新记录，只取所需要的id
    #update_chart = True
    join_room(str(msg['session_id']) + 'update')
    print user_ad_state

def history_data_filter(data_all, ids):
    global chart_node_interval
    # 回传数据包含所有选择的id，不存在的id则为空列表
    data_cluster = {}
    for key in ids:
        data_cluster[key] = []
    today_zero = datetime.datetime.strptime(datetime.datetime.now().strftime('%Y-%m-%d') + " 00:00:00", '%Y-%m-%d %H:%M:%S')
    for key, value in data_all.iteritems():
        if key in ids:
            # 补充当天0点到现在的历史数据，用0代替
            oldest_time = datetime.datetime.strptime(value[0][3], '%Y-%m-%d %H:%M:%S')
            while ((oldest_time - datetime.timedelta(seconds=chart_node_interval)) >= today_zero):
                value.insert(0, [0, 0, datetime.datetime.now().strftime('%Y-%m-%d'), (oldest_time - datetime.timedelta(seconds=chart_node_interval)).strftime('%Y-%m-%d %H:%M:%S')])
                oldest_time = datetime.datetime.strptime(value[0][3], '%Y-%m-%d %H:%M:%S')
            data_cluster[key] = value
    return data_cluster

def realtime_data_filter(data_realtime, ids):
    data_filtered = {}
    for key in ids:
        data_filtered[key] = []
    for key, value in data_realtime.iteritems():
        if key in ids:
            data_filtered[key] = value
    return data_filtered

@socketio.on_error('/test')
def error_handler_data(e):
    pass

if __name__ == '__main__':
    #app.run(host='0.0.0.0', port=5556, threaded=True)
    threading.Thread(target = test_message, args = (), name = 'data_getter_thread').start()
    socketio.run(app,"0.0.0.0",5556) 
