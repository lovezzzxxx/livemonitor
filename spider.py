#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import time
import copy
import re
import random
import struct
import threading
import websocket
import asyncio
import requests
import json
import zlib
from bs4 import BeautifulSoup
from urllib.parse import quote, unquote


# 仅从cfg和cfg_mod中获取参数，不会启动子监视器
class SubMonitor(threading.Thread):
    def __init__(self, name, tgt, tgt_name, cfg, **cfg_mod):
        super().__init__()
        self.name = name
        self.tgt = tgt
        self.tgt_name = tgt_name

        self.interval = 60
        self.vip_dic = {}
        self.word_dic = {}
        self.cookies = {}
        self.proxy = {}
        self.push_list = []
        # 不要直接修改通过cfg引用传递定义的列表和变量，请deepcopy后再修改
        for var in cfg:
            setattr(self, var, cfg[var])
        for var in cfg_mod:
            setattr(self, var, cfg_mod[var])

        self.stop_now = False

    def checksubmonitor(self):
        pass

    def run(self):
        while not self.stop_now:
            time.sleep(self.interval)

    def stop(self):
        self.stop_now = True


# 保留cfg(cfg_mod并不修改cfg本身)，可以启动子监视器
class Monitor(SubMonitor):
    # 初始化
    def __init__(self, name, tgt, tgt_name, cfg, **cfg_mod):
        super().__init__(name, tgt, tgt_name, cfg, **cfg_mod)
        self.cfg = copy.deepcopy(cfg)

        self.submonitor_config_name = "cfg"
        self.submonitor_threads = {}
        self.submonitor_cnt = 0
        self.submonitor_live_cnt = 0
        self.submonitor_checknow = False

        self.stop_now = False

    # 重设submonitorconfig名字并初始化
    def submonitorconfig_setname(self, submonitor_config_name):
        self.submonitor_config_name = submonitor_config_name
        try:
            submonitor_config = getattr(self, submonitor_config_name)
        except:
            submonitor_config = {"submonitor_dic": {}}
        setattr(self, self.submonitor_config_name, submonitor_config)

    # 向submonitorconfig添加预设的config
    def submonitorconfig_addconfig(self, config_name, config):
        submonitor_config = getattr(self, self.submonitor_config_name)
        submonitor_config[config_name] = config
        setattr(self, self.submonitor_config_name, submonitor_config)

    # 向submonitorconfig的submonitor_dic中添加子线程信息以启动子线程
    def submonitorconfig_addmonitor(self, monitor_name, monitor_class, monitor_target, monitor_target_name,
                                    monitor_config_name, **config_mod):
        submonitor_config = getattr(self, self.submonitor_config_name)
        if monitor_name not in submonitor_config["submonitor_dic"]:
            submonitor_config["submonitor_dic"][monitor_name] = {}
        submonitor_config["submonitor_dic"][monitor_name]["class"] = monitor_class
        submonitor_config["submonitor_dic"][monitor_name]["target"] = monitor_target
        submonitor_config["submonitor_dic"][monitor_name]["target_name"] = monitor_target_name
        submonitor_config["submonitor_dic"][monitor_name]["config_name"] = monitor_config_name
        for mod in config_mod:
            submonitor_config["submonitor_dic"][monitor_name][mod] = config_mod[mod]
        setattr(self, self.submonitor_config_name, submonitor_config)

    # 从submonitorconfig的submonitor_dic中删除对应的子线程
    def submonitorconfig_delmonitor(self, monitor_name):
        submonitor_config = getattr(self, self.submonitor_config_name)
        if monitor_name in submonitor_config["submonitor_dic"]:
            submonitor_config["submonitor_dic"].pop(monitor_name)
        setattr(self, self.submonitor_config_name, submonitor_config)

    # 按照submonitorconfig检查子线程池
    def checksubmonitor(self):
        if not self.submonitor_checknow:
            self.submonitor_checknow = True
            submonitorconfig = getattr(self, self.submonitor_config_name)
            if "submonitor_dic" in submonitorconfig:
                self.submonitor_cnt = len(submonitorconfig["submonitor_dic"])
                for monitor_name in submonitorconfig["submonitor_dic"]:
                    if monitor_name not in self.submonitor_threads:
                        # 按照submonitorconfig启动子线程并添加到子线程池
                        monitor_thread = createmonitor(monitor_name, submonitorconfig)
                        self.submonitor_threads[monitor_name] = monitor_thread

                self.submonitor_live_cnt = 0
                for monitor_name in list(self.submonitor_threads):
                    if monitor_name not in submonitorconfig["submonitor_dic"]:
                        # 按照submonitorconfig关闭子线程并清理子线程池
                        if self.submonitor_threads[monitor_name].is_alive():
                            self.submonitor_threads[monitor_name].stop()
                            self.submonitor_live_cnt += 1
                        else:
                            self.submonitor_threads.pop(monitor_name)
                    else:
                        # 从子线程池检查并重启
                        if self.submonitor_threads[monitor_name].is_alive():
                            self.submonitor_threads[monitor_name].checksubmonitor()
                            self.submonitor_live_cnt += 1
                        else:
                            self.submonitor_threads[monitor_name].stop()
                            monitor_thread = createmonitor(monitor_name, submonitorconfig)
                            self.submonitor_threads[monitor_name] = monitor_thread
                if self.submonitor_live_cnt > 0 or self.submonitor_cnt > 0:
                    printlog(
                        '[Check] "%s" 子线程运行情况：%s/%s' % (self.name, self.submonitor_live_cnt, self.submonitor_cnt))
            self.submonitor_checknow = False

    # 启动
    def run(self):
        self.checksubmonitor()
        while not self.stop_now:
            time.sleep(self.interval)

    # 停止线程
    def stop(self):
        self.stop_now = True
        for monitor_name in self.submonitor_threads:
            self.submonitor_threads[monitor_name].stop()


# vip=tgt, word=title+description, standby_chat="True"/"False", standby_chat_onstart="True"/"False", no_chat="True"/"False", status_push="等待|开始|结束|上传|删除", regen="False"/"间隔秒数", regen_amount="1"/"恢复数量"
class YoutubeLive(Monitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        # 重新设置submonitorconfig用于启动子线程，并添加频道id信息到子进程使用的cfg中
        self.submonitorconfig_setname("youtubechat_submonitor_cfg")
        self.submonitorconfig_addconfig("youtubechat_config", self.cfg)

        self.is_firstrun = True
        # video_id为字符
        self.videodic = {}
        # 是否检测待机直播间的弹幕
        try:
            getattr(self, "standby_chat")
        except:
            self.standby_chat = "False"
        # 是否检测在第一次检测时已开启的待机直播间的弹幕
        try:
            getattr(self, "standby_chat_onstart")
        except:
            self.standby_chat_onstart = "False"
        # 不记录弹幕
        try:
            getattr(self, "no_chat")
        except:
            self.no_chat = "False"
        # 需要推送的情况，其中等待|开始|结束是直播和首播才有的情况，上传是视频才有的情况，删除则都存在
        try:
            getattr(self, "status_push")
        except:
            self.status_push = "等待|开始|结束|上传|删除"
        # 推送惩罚恢复间隔
        try:
            getattr(self, "regen")
        except:
            self.regen = "False"
        # 每次推送惩罚恢复量
        try:
            getattr(self, "regen_amount")
        except:
            self.regen_amount = 1

    def run(self):
        while not self.stop_now:
            # 更新视频列表
            videodic_new = getyoutubevideodic(self.tgt, self.proxy)
            if isinstance(videodic_new, dict):
                for video_id in videodic_new:
                    if video_id not in self.videodic:
                        self.videodic[video_id] = videodic_new[video_id]
                        if not self.is_firstrun or videodic_new[video_id][
                            "video_status"] == "等待" and self.standby_chat_onstart == "True" or videodic_new[video_id][
                            "video_status"] == "开始":
                            self.push(video_id)
                writelog(self.logpath, '[Info] "%s" getyoutubevideodic %s firstresult\n%s' % (self.name, self.tgt, videodic_new))
                self.is_firstrun = False
                writelog(self.logpath, '[Success] "%s" getyoutubevideodic %s' % (self.name, self.tgt))
            else:
                printlog('[Error] "%s" getyoutubevideodic %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getyoutubevideodic %s' % (self.name, self.tgt))

            # 更新视频状态
            for video_id in self.videodic:
                if self.videodic[video_id]["video_status"] == "等待" or self.videodic[video_id]["video_status"] == "开始":
                    video_status = getyoutubevideostatus(video_id, self.proxy)
                    if video_status:
                        if self.videodic[video_id]["video_status"] != video_status:
                            self.videodic[video_id]["video_status"] = video_status
                            self.push(video_id)
                        writelog(self.logpath, '[Success] "%s" getyoutubevideostatus %s' % (self.name, video_id))
                    else:
                        printlog("[Error] %s getvideostatus %s" % (self.name, video_id))
                        writelog(self.logpath, '[Error] "%s" getyoutubevideostatus %s' % (self.name, video_id))
            time.sleep(self.interval)

    def push(self, video_id):
        if self.status_push.count(self.videodic[video_id]["video_status"]):
            # 获取视频简介
            video_description = getyoutubevideodescription(video_id, self.proxy)
            if isinstance(video_description, str):
                writelog(self.logpath,
                         '[Success] "%s" getyoutubevideodescription %s' % (self.name, video_id))
            else:
                printlog('[Error] "%s" getyoutubevideodescription %s' % (self.name, video_id))
                writelog(self.logpath, '[Error] "%s" getyoutubevideodescription %s' % (self.name, video_id))
                video_description = ""

            # 计算推送力度
            pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
            pushcolor_worddic = getpushcolordic("%s\n%s" % (self.videodic[video_id]["video_title"], video_description),
                                                self.word_dic)
            pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

            # 进行推送
            if pushcolor_dic:
                pushtext = "【%s %s %s%s】\n标题：%s\n时间：%s\n网址：https://www.youtube.com/watch?v=%s" % (
                    self.__class__.__name__, self.tgt_name, self.videodic[video_id]["video_type"],
                    self.videodic[video_id]["video_status"], self.videodic[video_id]["video_title"],
                    waittime(self.videodic[video_id]["video_timestamp"]), video_id)
                pushall(pushtext, pushcolor_dic, self.push_list)
                printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
                writelog(self.logpath,
                         '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

        if self.no_chat != "True":
            # 开始记录弹幕
            if self.videodic[video_id]["video_status"] == "等待" and self.standby_chat == "True" or \
                    self.videodic[video_id]["video_status"] == "开始":
                monitor_name = "%s - YoutubeChat %s" % (self.name, video_id)
                if monitor_name not in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                    self.submonitorconfig_addmonitor(monitor_name, "YoutubeChat", video_id, self.tgt_name,
                                                     "youtubechat_config", tgt_channel=self.tgt, interval=2,
                                                     regen=self.regen, regen_amount=self.regen_amount)
                    self.checksubmonitor()
                    printlog('[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
                    writelog(self.logpath, '[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
            # 停止记录弹幕
            else:
                monitor_name = "%s - YoutubeChat %s" % (self.name, video_id)
                if monitor_name in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                    self.submonitorconfig_delmonitor(monitor_name)
                    self.checksubmonitor()
                    printlog('[Info] "%s" stopsubmonitor %s' % (self.name, monitor_name))
                    writelog(self.logpath, '[Info] "%s" stopsubmonitor %s' % (self.name, monitor_name))


# vip=userchannel, word=text, punish=tgt+push(不包括含有'vip'的类型)
class YoutubeChat(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s/%s.txt' % (
            self.__class__.__name__, self.tgt_name, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)
        if not os.path.exists('./log/%s/%s' % (self.__class__.__name__, self.tgt_name)):
            os.mkdir('./log/%s/%s' % (self.__class__.__name__, self.tgt_name))

        # continuation为字符
        self.continuation = False
        self.pushpunish = {}
        self.regen_time = 0
        try:
            getattr(self, "tgt_channel")
        except:
            self.tgt_channel = ""
        try:
            self.regen = self.regen
        except:
            self.regen = "False"
        try:
            self.regen_amount = self.regen_amount
        except:
            self.regen_amount = 1

    def run(self):
        while not self.stop_now:
            # 获取continuation
            if not self.continuation:
                self.continuation = getyoutubechatcontinuation(self.tgt, self.proxy)
                if self.continuation:
                    writelog(self.logpath, '[Success] "%s" getyoutubechatcontinuation %s' % (self.name, self.tgt))
                else:
                    printlog('[Error] "%s" getyoutubechatcontinuation %s' % (self.name, self.tgt))
                    writelog(self.logpath, '[Error] "%s" getyoutubechatcontinuation %s' % (self.name, self.tgt))
                    time.sleep(5)
                    continue

            # 获取直播评论列表
            if self.continuation:
                chatlist, self.continuation = getyoutubechatlist(self.continuation, self.proxy)
                if isinstance(chatlist, list):
                    for chat in chatlist:
                        self.push(chat)

                    # 目标每次请求获取5条评论，间隔时间应在0.1~2秒之间
                    if len(chatlist) > 0:
                        self.interval = self.interval * 5 / len(chatlist)
                    else:
                        self.interval = 2
                    if self.interval > 2:
                        self.interval = 2
                    if self.interval < 0.1:
                        self.interval = 0.1
                else:
                    printlog('[Error] "%s" getyoutubechatlist %s' % (self.name, self.continuation))
                    writelog(self.logpath, '[Error] "%s" getyoutubechatlist %s' % (self.name, self.continuation))
            time.sleep(self.interval)

    def push(self, chat):
        writelog(self.logpath, "%s\t%s(%s)\t(%s)%s" % (
            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(chat["chat_timestamp"])),
            chat["chat_username"], chat["chat_userchannel"], chat["chat_type"], chat["chat_text"]))

        pushcolor_vipdic = getpushcolordic(chat["chat_userchannel"], self.vip_dic)
        pushcolor_worddic = getpushcolordic(chat["chat_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushcolor_dic = self.punish(pushcolor_dic)

            pushtext = "【%s %s 直播评论】\n用户：%s\n内容：%s\n类型：%s\n网址：https://www.youtube.com/watch?v=%s" % (
                self.__class__.__name__, self.tgt_name, chat["chat_username"], chat["chat_text"], chat["chat_type"],
                self.tgt)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

    def punish(self, pushcolor_dic):
        # 推送惩罚恢复
        if self.regen != "False":
            time_now = round(time.time())
            regen_amt = int((time_now - self.regen_time) / int(self.regen)) * int(self.regen_amount)
            if regen_amt:
                self.regen_time = time_now
                for color in list(self.pushpunish):
                    if self.pushpunish[color] > regen_amt:
                        self.pushpunish[color] -= regen_amt
                    else:
                        self.pushpunish.pop(color)

        # 去除来源频道的相关权重
        if self.tgt_channel in self.vip_dic:
            for color in self.vip_dic[self.tgt_channel]:
                if color in pushcolor_dic and not color.count("vip"):
                    pushcolor_dic[color] -= self.vip_dic[self.tgt_channel][color]

        # 只对pushcolor_dic存在的键进行修改，不同于addpushcolordic
        for color in self.pushpunish:
            if color in pushcolor_dic and not color.count("vip"):
                pushcolor_dic[color] -= self.pushpunish[color]

        # 更新pushpunish
        for color in pushcolor_dic:
            if pushcolor_dic[color] > 0 and not color.count("vip"):
                if color in self.pushpunish:
                    self.pushpunish[color] += 1
                else:
                    self.pushpunish[color] = 1
        return pushcolor_dic


# vip=tgt, word=text
class YoutubeCom(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        # post_id为字符
        self.postlist = []

    def run(self):
        while not self.stop_now:
            # 获取帖子列表
            postdic_new = getyoutubepostdic(self.tgt, self.cookies, self.proxy)
            if isinstance(postdic_new, dict):
                for post_id in postdic_new:
                    if post_id not in self.postlist:
                        self.postlist.append(post_id)
                        if not self.is_firstrun:
                            self.push(post_id, postdic_new)
                writelog(self.logpath, '[Info] "%s" getyoutubepostdic %s firstresult\n%s' % (self.name, self.tgt, postdic_new))
                writelog(self.logpath, '[Success] "%s" getyoutubepostdic %s' % (self.name, self.tgt))
                self.is_firstrun = False
            else:
                printlog('[Error] "%s" getyoutubepostdic %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getyoutubepostdic %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, post_id, postdic):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_worddic = getpushcolordic(postdic[post_id]["post_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        # 进行推送
        if pushcolor_dic:
            pushtext = "【%s %s 社区帖子】\n内容：%s\n时间：%s\n网址：https://www.youtube.com/post/%s" % (
                self.__class__.__name__, self.tgt_name, postdic[post_id]["post_text"][0:3000],
                postdic[post_id]["post_time"], post_id)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# word=text
class YoutubeNote(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.token = False
        # note_id为整数
        self.note_id_old = 0

    def run(self):
        while not self.stop_now:
            # 获取token
            if not self.token:
                self.token = getyoutubetoken(self.cookies, self.proxy)
                if self.token:
                    writelog(self.logpath, '[Success] "%s" getyoutubetoken %s' % (self.name, self.tgt))
                else:
                    printlog('[Error] "%s" getyoutubetoken %s' % (self.name, self.tgt))
                    writelog(self.logpath, '[Error] "%s" getyoutubetoken %s' % (self.name, self.tgt))
                    time.sleep(5)
                    continue

            # 获取订阅通知列表
            if self.token:
                notedic_new = getyoutubenotedic(self.token, self.cookies, self.proxy)
                if isinstance(notedic_new, dict):
                    if notedic_new:
                        if self.is_firstrun:
                            try:
                                self.note_id_old = sorted(notedic_new, reverse=True)[0]
                            except:
                                pass
                            writelog(self.logpath, '[Info] "%s" getyoutubenotedic %s firstresult\n%s' % (self.name, self.tgt, notedic_new))
                            self.is_firstrun = False
                        else:
                            for note_id in notedic_new:
                                if note_id > self.note_id_old:
                                    self.push(note_id, notedic_new)
                            try:
                                self.note_id_old = sorted(notedic_new, reverse=True)[0]
                            except:
                                pass
                    writelog(self.logpath, '[Success] "%s" getyoutubenotedic %s' % (self.name, self.tgt))
                else:
                    printlog('[Error] "%s" getyoutubenotedic %s' % (self.name, self.tgt))
                    writelog(self.logpath, '[Error] "%s" getyoutubenotedic %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, note_id, notedic):
        pushcolor_worddic = getpushcolordic(notedic[note_id]["note_text"], self.word_dic)
        pushcolor_dic = pushcolor_worddic

        if pushcolor_dic:
            pushtext = "【%s %s 订阅通知】\n内容：%s\n时间：%s\n网址：https://www.youtube.com/watch?v=%s" % (
                self.__class__.__name__, self.tgt_name, notedic[note_id]["note_text"],
                notedic[note_id]["note_time"], notedic[note_id]["note_videoid"])
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt, no_increase="True"/"False"
class TwitterUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        # 是否不推送推文和媒体数量的增加
        try:
            getattr(self, "no_increase")
        except:
            self.no_increase = "False"

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            user_datadic_new = gettwitteruser(self.tgt, self.cookies, self.proxy)
            if isinstance(user_datadic_new, dict):
                if self.is_firstrun:
                    self.userdata_dic = user_datadic_new
                    writelog(self.logpath, '[Info] "%s" gettwitteruser %s firstresult\n%s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        # 不可能会增加新键所以不做判断
                        if self.userdata_dic[key] != user_datadic_new[key]:
                            if self.no_increase == "True" and (key == "user_twitcount" or key == "user_mediacount"):
                                if self.userdata_dic[key] < user_datadic_new[key]:
                                    self.userdata_dic[key] = user_datadic_new[key]
                                    continue

                            pushtext_body += "键：%s\n原值：%s\n现值：%s\n\n" % (
                                key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                    
                    if pushtext_body:
                        self.push(pushtext_body)
                writelog(self.logpath, '[Success] "%s" gettwitteruser %s' % (self.name, self.tgt))
            else:
                printlog('[Error] "%s" gettwitteruser %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" gettwitteruser %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, pushtext_body):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushtext = "【%s %s 数据改变】\n%s网址：https://twitter.com/%s" % (
                self.__class__.__name__, self.tgt_name, pushtext_body, self.tgt)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt+mention, word=text
class TwitterTweet(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.tgt_restid = False
        # tweet_id为整数
        self.tweet_id_old = 0

    def run(self):
        while not self.stop_now:
            # 获取用户restid
            if not self.tgt_restid:
                tgt_dic = gettwitteruser(self.tgt, self.cookies, self.proxy)
                if isinstance(tgt_dic, dict):
                    self.tgt_restid = tgt_dic["user_restid"]
                    writelog(self.logpath, '[Success] "%s" gettwitteruser %s' % (self.name, self.tgt))
                else:
                    printlog('[Error] "%s" gettwitteruser %s' % (self.name, self.tgt))
                    writelog(self.logpath, '[Error] "%s" gettwitteruser %s' % (self.name, self.tgt))
                    time.sleep(5)
                    continue

            # 获取推特列表
            if self.tgt_restid:
                tweetdic_new = gettwittertweetdic(self.tgt_restid, self.cookies, self.proxy)
                if isinstance(tweetdic_new, dict):
                    if tweetdic_new:
                        if self.is_firstrun:
                            try:
                                self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                            except:
                                pass
                            writelog(self.logpath, '[Info] "%s" gettwittertweetdic %s firstresult\n%s' % (self.name, self.tgt, tweetdic_new))
                            self.is_firstrun = False
                        else:
                            for tweet_id in tweetdic_new:
                                if tweet_id > self.tweet_id_old:
                                    self.push(tweet_id, tweetdic_new)
                            try:
                                self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                            except:
                                pass
                    writelog(self.logpath, '[Success] "%s" gettwittertweetdic %s' % (self.name, self.tgt_restid))
                else:
                    printlog('[Error] "%s" gettwittertweetdic %s' % (self.name, self.tgt_restid))
                    writelog(self.logpath, '[Error] "%s" gettwittertweetdic %s' % (self.name, self.tgt_restid))
            time.sleep(self.interval)

    def push(self, tweet_id, tweetdic):
        # 获取用户推特时大小写不敏感，但检测用户和提及的时候大小写敏感
        pushcolor_vipdic = getpushcolordic("%s\n%s" % (self.tgt, tweetdic[tweet_id]['tweet_mention']),
                                           self.vip_dic)
        pushcolor_worddic = getpushcolordic(tweetdic[tweet_id]['tweet_text'], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushtext = "【%s %s 推特%s】\n内容：%s\n媒体：%s\n链接：%s\n时间：%s (GMT)\n网址：https://twitter.com/%s/status/%s" % (
                self.__class__.__name__, self.tgt_name, tweetdic[tweet_id]["tweet_type"],
                tweetdic[tweet_id]["tweet_text"], tweetdic[tweet_id]["tweet_media"], tweetdic[tweet_id]["tweet_urls"],
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(tweetdic[tweet_id]["tweet_timestamp"])), self.tgt,
                tweet_id)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt+mention, word=text, only_live="True"/"False", only_liveorvideo="True"/"False", "no_chat"="True"/"False"
class TwitterSearch(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.tweet_id_old = 0
        # 是否只推送有链接指向正在进行的youtube直播的推文
        try:
            getattr(self, "only_live")
        except:
            self.only_live = "False"
        # 是否只推送有链接指向youtube直播或视频的推文
        try:
            getattr(self, "only_liveorvideo")
        except:
            self.only_liveorvideo = "False"

    def run(self):
        while not self.stop_now:
            # 获取推特列表
            tweetdic_new = gettwittersearchdic(self.tgt, self.cookies, self.proxy)
            if isinstance(tweetdic_new, dict):
                if tweetdic_new:
                    if self.is_firstrun:
                        try:
                            self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                        except:
                            pass
                        writelog(self.logpath, '[Info] "%s" gettwittersearchdic %s firstresult\n%s' % (self.name, self.tgt, tweetdic_new))
                        self.is_firstrun = False
                    else:
                        for tweet_id in tweetdic_new:
                            if tweet_id > self.tweet_id_old:
                                self.push(tweet_id, tweetdic_new)
                        try:
                            self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                        except:
                            pass
                writelog(self.logpath, '[Success] "%s" gettwittersearchdic %s' % (self.name, self.tgt))
            else:
                printlog('[Error] "%s" gettwittersearchdic %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" gettwittersearchdic %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, tweet_id, tweetdic):
        # 检测是否有链接指向正在进行的直播
        if self.only_live == "True":
            is_live = False
            for url in tweetdic[tweet_id]["tweet_urls"]:
                if url.count("https://youtu.be/"):
                    if getyoutubevideostatus(url.replace("https://youtu.be/", ""), self.proxy) == "开始":
                        is_live = True
                        break
        else:
            is_live = True

        # 检测是否有链接指向直播或视频
        if self.only_liveorvideo == "True":
            is_liveorvideo = False
            for url in tweetdic[tweet_id]["tweet_urls"]:
                if url.count("https://youtu.be/"):
                    is_liveorvideo = True
                    break
        else:
            is_liveorvideo = True

        if is_live and is_liveorvideo:
            pushcolor_vipdic = getpushcolordic("%s\n%s" % (self.tgt, tweetdic[tweet_id]['tweet_mention']),
                                               self.vip_dic)
            pushcolor_worddic = getpushcolordic(tweetdic[tweet_id]['tweet_text'], self.word_dic)
            pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

            if pushcolor_dic:
                pushtext = "【%s %s 推特%s】\n内容：%s\n媒体：%s\n链接：%s\n时间：%s (GMT)\n网址：https://twitter.com/a/status/%s" % (
                    self.__class__.__name__, self.tgt_name, tweetdic[tweet_id]["tweet_type"],
                    tweetdic[tweet_id]["tweet_text"], tweetdic[tweet_id]["tweet_media"],
                    tweetdic[tweet_id]["tweet_urls"],
                    time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(tweetdic[tweet_id]["tweet_timestamp"])), tweet_id)
                pushall(pushtext, pushcolor_dic, self.push_list)
                printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
                writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt, "no_chat"="True"/"False", "status_push" = "开始|结束", regen="False"/"间隔秒数", regen_amount="1"/"恢复数量"
class TwitcastLive(Monitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        # 重新设置submonitorconfig用于启动子线程，并添加频道id信息到子进程使用的cfg中
        self.submonitorconfig_setname("twitcastchat_submonitor_cfg")
        self.submonitorconfig_addconfig("twitcastchat_config", self.cfg)

        self.livedic = {"": {"live_status": "结束", "live_title": ""}}
        try:
            getattr(self, "no_chat")
        except:
            self.no_chat = "False"
        try:
            getattr(self, "status_push")
        except:
            self.status_push = "开始|结束"
        try:
            getattr(self, "regen")
        except:
            self.regen = "False"
        try:
            getattr(self, "regen_amount")
        except:
            self.regen_amount = 1

    def run(self):
        while not self.stop_now:
            # 获取直播状态
            livedic_new = gettwitcastlive(self.tgt, self.proxy)
            if isinstance(livedic_new, dict):
                for live_id in livedic_new:
                    if live_id not in self.livedic or livedic_new[live_id]["live_status"] == "结束":
                        for live_id_old in self.livedic:
                            if self.livedic[live_id_old]["live_status"] != "结束":
                                self.livedic[live_id_old]["live_status"] = "结束"
                                self.push(live_id_old)

                    if live_id not in self.livedic:
                        self.livedic[live_id] = livedic_new[live_id]
                        self.push(live_id)
                    # 返回非空的live_id则必定为正在直播的状态，不过还是保留防止问题
                    elif self.livedic[live_id]["live_status"] != livedic_new[live_id]["live_status"]:
                        self.livedic[live_id] = livedic_new[live_id]
                        self.push(live_id)
                writelog(self.logpath, '[Success] "%s" gettwitcastlive %s' % (self.name, self.tgt))
            else:
                printlog('[Error] "%s" gettwitcastlive %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" gettwitcastlive %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, live_id):
        if self.status_push.count(self.livedic[live_id]["live_status"]):
            pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
            pushcolor_worddic = getpushcolordic(self.livedic[live_id]["live_title"], self.word_dic)
            pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

            if pushcolor_dic:
                pushtext = "【%s %s 直播%s】\n标题：%s\n网址：https://twitcasting.tv/%s" % (
                    self.__class__.__name__, self.tgt_name, self.livedic[live_id]["live_status"],
                    self.livedic[live_id]["live_title"], self.tgt)
                pushall(pushtext, pushcolor_dic, self.push_list)
                printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
                writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

        if self.no_chat != "True":
            # 开始记录弹幕
            if self.livedic[live_id]["live_status"] == "开始":
                monitor_name = "%s - TwitcastChat %s" % (self.name, live_id)
                if monitor_name not in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                    self.submonitorconfig_addmonitor(monitor_name, "TwitcastChat", live_id, self.tgt_name,
                                                     "twitcastchat_config", tgt_channel=self.tgt, interval=2,
                                                     regen=self.regen, regen_amount=self.regen_amount)
                    self.checksubmonitor()
                    printlog('[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
                    writelog(self.logpath, '[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
            # 停止记录弹幕
            else:
                monitor_name = "%s - TwitcastChat %s" % (self.name, live_id)
                if monitor_name in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                    self.submonitorconfig_delmonitor(monitor_name)
                    self.checksubmonitor()
                    printlog('[Info] "%s" stopsubmonitor %s' % (self.name, monitor_name))
                    writelog(self.logpath, '[Info] "%s" stopsubmonitor %s' % (self.name, monitor_name))


# vip=chat_screenname, word=text, punish=tgt+push(不包括含有'vip'的类型)
class TwitcastChat(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s/%s.txt' % (
            self.__class__.__name__, self.tgt_name, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)
        if not os.path.exists('./log/%s/%s' % (self.__class__.__name__, self.tgt_name)):
            os.mkdir('./log/%s/%s' % (self.__class__.__name__, self.tgt_name))

        self.chat_id_old = 0
        self.pushpunish = {}
        self.regen_time = 0
        try:
            getattr(self, "tgt_channel")
        except:
            self.tgt_channel = ""
        try:
            self.regen = self.regen
        except:
            self.regen = "False"
        try:
            self.regen_amount = self.regen_amount
        except:
            self.regen_amount = 1

    def run(self):
        while not self.stop_now:
            # 获取直播评论列表
            chatlist = gettwitcastchatlist(self.tgt, self.proxy)
            if isinstance(chatlist, list):
                for chat in chatlist:
                    # chatlist默认从小到大排列
                    if self.chat_id_old < chat['chat_id']:
                        self.chat_id_old = chat['chat_id']
                        self.push(chat)

                # 目标每次请求获取5条评论，间隔时间应在0.1~2秒之间
                if len(chatlist) > 0:
                    self.interval = self.interval * 5 / len(chatlist)
                else:
                    self.interval = 2
                if self.interval > 2:
                    self.interval = 2
                if self.interval < 0.1:
                    self.interval = 0.1
            else:
                printlog('[Error] "%s" gettwitcastchatlist %s' % (self.name, self.chat_id_old))
                writelog(self.logpath, '[Error] "%s" gettwitcastchatlist %s' % (self.name, self.chat_id_old))
            time.sleep(self.interval)

    def push(self, chat):
        writelog(self.logpath, "%s\t%s(%s)\t%s" % (
            time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(round(int(chat["chat_timestamp"]) / 1000))),
            chat["chat_name"], chat["chat_screenname"], chat["chat_text"]))

        pushcolor_vipdic = getpushcolordic(chat["chat_screenname"], self.vip_dic)
        pushcolor_worddic = getpushcolordic(chat["chat_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushcolor_dic = self.punish(pushcolor_dic)

            pushtext = "【%s %s 直播评论】\n用户：%s(%s)\n内容：%s\n网址：https://twitcasting.tv/%s" % (
                self.__class__.__name__, self.tgt_name, chat["chat_name"], chat["chat_screenname"], chat["chat_text"],
                self.tgt_channel)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

    def punish(self, pushcolor_dic):
        if self.regen != "False":
            time_now = round(time.time())
            regen_amt = int((time_now - self.regen_time) / int(self.regen)) * int(self.regen_amount)
            if regen_amt:
                self.regen_time = time_now
                for color in list(self.pushpunish):
                    if self.pushpunish[color] > regen_amt:
                        self.pushpunish[color] -= regen_amt
                    else:
                        self.pushpunish.pop(color)

        if self.tgt_channel in self.vip_dic:
            for color in self.vip_dic[self.tgt_channel]:
                if color in pushcolor_dic and not color.count("vip"):
                    pushcolor_dic[color] -= self.vip_dic[self.tgt_channel][color]

        for color in self.pushpunish:
            if color in pushcolor_dic and not color.count("vip"):
                pushcolor_dic[color] -= self.pushpunish[color]

        for color in pushcolor_dic:
            if pushcolor_dic[color] > 0 and not color.count("vip"):
                if color in self.pushpunish:
                    self.pushpunish[color] += 1
                else:
                    self.pushpunish[color] = 1
        return pushcolor_dic


# vip=tgt
class FanboxUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            user_datadic_new = getfanboxuser(self.tgt, self.proxy)
            if isinstance(user_datadic_new, dict):
                if self.is_firstrun:
                    self.userdata_dic = user_datadic_new
                    writelog(self.logpath, '[Info] "%s" getfanboxuser %s firstresult\n%s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        # 不可能会增加新键所以不做判断
                        if self.userdata_dic[key] != user_datadic_new[key]:
                            pushtext_body += "键：%s\n原值：%s\n现值：%s\n\n" % (
                                key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                    
                    if pushtext_body:
                        self.push(pushtext_body)
                writelog(self.logpath, '[Success] "%s" getfanboxuser %s' % (self.name, self.tgt))
            else:
                printlog('[Error] "%s" getfanboxuser %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getfanboxuser %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, pushtext_body):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushtext = "【%s %s 数据改变】\n%s网址：https://twitter.com/%s" % (
                self.__class__.__name__, self.tgt_name, pushtext_body, self.tgt)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt, word=text
class FanboxPost(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.postlist = []

    def run(self):
        while not self.stop_now:
            # 获取帖子列表
            postdic_new = getfanboxpostdic(self.tgt, self.cookies, self.proxy)
            if isinstance(postdic_new, dict):
                for post_id in postdic_new:
                    if post_id not in self.postlist:
                        self.postlist.append(post_id)
                        if not self.is_firstrun:
                            self.push(post_id, postdic_new)
                writelog(self.logpath, '[Info] "%s" getfanboxpostdic %s firstresult\n%s' % (self.name, self.tgt, postdic_new))
                writelog(self.logpath, '[Success] "%s" getfanboxpostdic %s' % (self.name, self.tgt))
                self.is_firstrun = False
            else:
                printlog('[Error] "%s" getfanboxpostdic %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getfanboxpostdic %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, post_id, postdic):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_worddic = getpushcolordic(postdic[post_id]["post_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushtext = "【%s %s 社区帖子】\n标题：%s\n内容：%s\n类型：%s\n档位：%s\n时间：%s (GMT)\n网址：https://www.pixiv.net/fanbox/creator/%s/post/%s" % (
                self.__class__.__name__, self.tgt_name, postdic[post_id]["post_title"],
                postdic[post_id]["post_text"][0:2500],
                postdic[post_id]["post_type"], postdic[post_id]['post_fee'],
                time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(postdic[post_id]["post_publishtimestamp"])), self.tgt,
                post_id)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt, "offline_chat"="True"/"False", "simple_mode"="True"/"False"/"合并数量", "no_chat"="True"/"False", "status_push" = "开始|结束", regen="False"/"间隔秒数", regen_amount="1"/"恢复数量"
class BilibiliLive(Monitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        # 重新设置submonitorconfig用于启动子线程，并添加频道id信息到子进程使用的cfg中
        self.submonitorconfig_setname("bilibilichat_submonitor_cfg")
        self.submonitorconfig_addconfig("bilibilichat_config", self.cfg)

        self.livedic = {"": {"live_status": "结束", "live_title": ""}}
        try:
            getattr(self, "offline_chat")
        except:
            self.offline_chat = "False"
        try:
            getattr(self, "simple_mode")
        except:
            self.simple_mode = "False"
        try:
            getattr(self, "no_chat")
        except:
            self.no_chat = "False"
        try:
            getattr(self, "status_push")
        except:
            self.status_push = "开始|结束"
        try:
            getattr(self, "regen")
        except:
            self.regen = "False"
        try:
            getattr(self, "regen_amount")
        except:
            self.regen_amount = 1

    def run(self):
        if self.offline_chat == "True" and self.no_chat != "True":
            monitor_name = "%s - BilibiliChat %s" % (self.name, 'offline_chat')
            if monitor_name not in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                self.submonitorconfig_addmonitor(monitor_name, "BilibiliChat", self.tgt, self.tgt_name,
                                                 "bilibilichat_config", simple_mode=self.simple_mode)
                self.checksubmonitor()
            printlog('[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
            writelog(self.logpath, '[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))

        while not self.stop_now:
            # 获取直播状态
            livedic_new = getbilibililivedic(self.tgt, self.proxy)
            if isinstance(livedic_new, dict):
                for live_id in livedic_new:
                    if live_id not in self.livedic or livedic_new[live_id]["live_status"] == "结束":
                        for live_id_old in self.livedic:
                            if self.livedic[live_id_old]["live_status"] != "结束":
                                self.livedic[live_id_old]["live_status"] = "结束"
                                self.push(live_id_old)

                    if live_id not in self.livedic:
                        self.livedic[live_id] = livedic_new[live_id]
                        self.push(live_id)
                    elif self.livedic[live_id]["live_status"] != livedic_new[live_id]["live_status"]:
                        self.livedic[live_id] = livedic_new[live_id]
                        self.push(live_id)
                writelog(self.logpath, '[Success] "%s" getbilibililivedic %s' % (self.name, self.tgt))
            else:
                printlog('[Error] "%s" getbilibililivedic %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getbilibililivedic %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, live_id):
        if self.status_push.count(self.livedic[live_id]["live_status"]):
            pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
            pushcolor_worddic = getpushcolordic(self.livedic[live_id]["live_title"], self.word_dic)
            pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

            if pushcolor_dic:
                pushtext = "【%s %s 直播%s】\n标题：%s\n网址：https://live.bilibili.com/%s" % (
                    self.__class__.__name__, self.tgt_name, self.livedic[live_id]["live_status"],
                    self.livedic[live_id]["live_title"], self.tgt)
                pushall(pushtext, pushcolor_dic, self.push_list)
                printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
                writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

        if self.offline_chat != "True" and self.no_chat != "True":
            # 开始记录弹幕
            if self.livedic[live_id]["live_status"] == "开始":
                monitor_name = "%s - BilibiliChat %s" % (self.name, live_id)
                if monitor_name not in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                    self.submonitorconfig_addmonitor(monitor_name, "BilibiliChat", self.tgt, self.tgt_name,
                                                     "bilibilichat_config", simple_mode=self.simple_mode,
                                                     regen=self.regen, regen_amount=self.regen_amount)
                    self.checksubmonitor()
                printlog('[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
                writelog(self.logpath, '[Info] "%s" startsubmonitor %s' % (self.name, monitor_name))
            # 停止记录弹幕
            else:
                monitor_name = "%s - BilibiliChat %s" % (self.name, live_id)
                if monitor_name in getattr(self, self.submonitor_config_name)["submonitor_dic"]:
                    self.submonitorconfig_delmonitor(monitor_name)
                    self.checksubmonitor()
                    printlog('[Info] "%s" stopsubmonitor %s' % (self.name, monitor_name))
                    writelog(self.logpath, '[Info] "%s" stopsubmonitor %s' % (self.name, monitor_name))


# vip=userid, word=text, punish=tgt+push(不包括含有'vip'的类型), 获取弹幕的websocket连接只能使用http proxy
class BilibiliChat(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s/%s.txt' % (
            self.__class__.__name__, self.tgt_name, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)
        if not os.path.exists('./log/%s/%s' % (self.__class__.__name__, self.tgt_name)):
            os.mkdir('./log/%s/%s' % (self.__class__.__name__, self.tgt_name))

        try:
            getattr(self, "simple_mode")
        except:
            self.simple_mode = "False"
        if self.simple_mode != "False":
            self.pushcount = 0
            self.pushtext_old = ""
            # self.pushtext_old = "【%s %s】\n" % (self.__class__.__name__, self.tgt_name)
            self.pushcolor_dic_old = {}
            try:
                self.simple_mode = int(self.simple_mode)
                if self.simple_mode == 0:
                    self.simple_mode = 1
            except:
                self.simple_mode = 1
        self.proxyhost = ""
        self.proxyport = ""
        if 'http' in self.proxy:
            proxysplit = self.proxy['http'].split(':')
            if len(proxysplit) == 2:
                self.proxyhost = proxysplit[0]
                self.proxyport = proxysplit[1]

        self.hostlist = []
        self.hostcount = 1
        self.ws = False
        self.is_linked = False
        self.pushpunish = {}
        self.regen_time = 0
        try:
            self.regen = self.regen
        except:
            self.regen = "False"
        try:
            self.regen_amount = self.regen_amount
        except:
            self.regen_amount = 1

    def getpacket(self, data, operation):
        '''
        packet_length, header_length, protocol_version, operation, sequence_id
        
        HANDSHAKE=0, HANDSHAKE_REPLY = 1, HEARTBEAT = 2, HEARTBEAT_REPLY = 3, SEND_MSG = 4
        SEND_MSG_REPLY = 5, DISCONNECT_REPLY = 6, AUTH = 7, AUTH_REPLY = 8
        RAW = 9, PROTO_READY = 10, PROTO_FINISH = 11, CHANGE_ROOM = 12
        CHANGE_ROOM_REPLY = 13, REGISTER = 14, REGISTER_REPLY = 15, UNREGISTER = 16, UNREGISTER_REPLY = 17
        '''
        body = json.dumps(data).encode('utf-8')
        header = struct.pack('>I2H2I', 16 + len(body), 16, 1, operation, 1)
        return header + body

    def prasepacket(self, packet):
        try:
            packet = zlib.decompress(packet[16:])
        except:
            pass

        packetlist = []
        offset = 0
        while offset < len(packet):
            try:
                header = packet[offset:offset + 16]
                headertuple = struct.Struct('>I2H2I').unpack_from(header)
                packet_length = headertuple[0]
                operation = headertuple[3]

                body = packet[offset + 16:offset + packet_length]
                try:
                    data = json.loads(body.decode('utf-8'))
                    packetlist.append({"data": data, "operation": operation})
                except:
                    packetlist.append({"data": body, "operation": operation})

                offset += packet_length
            except:
                continue
        return packetlist

    def heartbeat(self):
        while not self.stop_now:
            if self.is_linked:
                self.ws.send(self.getpacket({}, 2))
                time.sleep(30)
            else:
                time.sleep(1)

    def parsedanmu(self, chat_json):
        try:
            chat_cmd = chat_json['cmd']
            '''
            if chat_cmd == 'LIVE': # 直播开始
            if chat_cmd == 'PREPARING': # 直播停止
            if chat_cmd == 'WELCOME':
                chat_user = chat_json['data']['uname']
            '''
            if chat_cmd == 'DANMU_MSG':
                chat_type = 'message'
                chat_text = chat_json['info'][1]
                chat_userid = str(chat_json['info'][2][0])
                chat_username = chat_json['info'][2][1]
                # chat_isadmin = dic['info'][2][2] == '1'
                # chat_isvip = dic['info'][2][3] == '1'
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username}
                self.push(chat)
            elif chat_cmd == 'SEND_GIFT':
                chat_type = 'gift %s %s' % (chat_json['data']['giftName'], chat_json['data']['num'])
                chat_text = ''
                chat_userid = str(chat_json['data']['uid'])
                chat_username = chat_json['data']['uname']
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username}
                self.push(chat)
            elif chat_cmd == 'SUPER_CHAT_MESSAGE':
                chat_type = 'superchat CN¥%s' % chat_json['data']['price']
                chat_text = chat_json['data']['message']
                chat_userid = str(chat_json['data']['uid'])
                chat_username = chat_json['data']['user_info']['uname']
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username}
                self.push(chat)
        except:
            pass

    def on_open(self):
        # 未登录uid则为0，注意int和str类有区别，protover为1则prasepacket中无需用zlib解压
        auth_data = {
            'uid': 0,
            'roomid': int(self.tgt),
            'protover': 2,
            'platform': 'web',
            'clientver': '1.10.3',
            'type': 2,
            'key':
                requests.get('https://api.live.bilibili.com/room/v1/Danmu/getConf', proxies=self.proxy).json()['data'][
                    'token']
        }
        self.ws.send(self.getpacket(auth_data, 7))
        writelog(self.logpath, '[Start] "%s" connect %s' % (self.name, self.tgt))

    def on_message(self, message):
        packetlist = self.prasepacket(message)

        for packet in packetlist:
            if packet["operation"] == 8:
                self.is_linked = True
                writelog(self.logpath, '[Success] "%s" connected %s' % (self.name, self.tgt))

            if packet["operation"] == 5:
                if isinstance(packet["data"], dict):
                    self.parsedanmu(packet["data"])

    def on_error(self, error):
        writelog(self.logpath, '[Error] "%s" error %s: %s' % (self.name, self.tgt, error))

    def on_close(self):
        # 推送剩余的弹幕
        if self.simple_mode != "False":
            if self.pushtext_old:
                pushall(self.pushtext_old, self.pushcolor_dic_old, self.push_list)
                printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(self.pushcolor_dic_old), self.pushtext_old))
                writelog(self.logpath,
                         '[Info] "%s" pushall %s\n%s' % (self.name, str(self.pushcolor_dic_old), self.pushtext_old))

                self.pushtext_old = ""
                # self.pushtext_old = "【%s %s】\n" % (self.__class__.__name__, self.tgt_name)

        self.is_linked = False
        writelog(self.logpath, '[Stop] "%s" disconnect %s' % (self.name, self.tgt))

    def run(self):
        # 启动heartbeat线程
        heartbeat_thread = threading.Thread(target=self.heartbeat, args=())
        heartbeat_thread.Daemon = True
        heartbeat_thread.start()

        while not self.stop_now:
            if self.hostcount < len(self.hostlist):
                host = self.hostlist[self.hostcount]
                self.hostcount += 1

                self.ws = websocket.WebSocketApp(host, on_open=self.on_open, on_message=self.on_message,
                                                 on_error=self.on_error, on_close=self.on_close)
                self.ws.run_forever(http_proxy_host=self.proxyhost, http_proxy_port=self.proxyport)
            else:
                self.hostlist = getbilibilichathostlist(self.proxy)
                self.hostcount = 0

                if self.hostlist:
                    writelog(self.logpath, '[Success] "%s" getbilibilichathostlist %s' % (self.name, self.tgt))
                else:
                    time.sleep(5)
                    printlog('[Error] "%s" getbilibilichathostlist %s' % (self.name, self.tgt))
                    writelog(self.logpath, '[Error] "%s" getbilibilichathostlist %s' % (self.name, self.tgt))

    def push(self, chat):
        writelog(self.logpath,
                 "%s(%s)\t(%s)%s" % (chat["chat_username"], chat["chat_userid"], chat["chat_type"], chat["chat_text"]))

        pushcolor_vipdic = getpushcolordic(chat["chat_userid"], self.vip_dic)
        pushcolor_worddic = getpushcolordic(chat["chat_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushcolor_dic = self.punish(pushcolor_dic)

            if self.simple_mode == "False":
                pushtext = "【%s %s 直播评论】\n用户：%s(%s)\n内容：%s\n类型：%s\n网址：https://live.bilibili.com/%s" % (
                    self.__class__.__name__, self.tgt_name, chat["chat_username"], chat["chat_userid"],
                    chat["chat_text"], chat["chat_type"], self.tgt)
                pushall(pushtext, pushcolor_dic, self.push_list)
                printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
                writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            else:
                self.pushcount += 1
                self.pushtext_old += chat["chat_text"]
                for color in pushcolor_dic:
                    if color in self.pushcolor_dic_old:
                        if self.pushcolor_dic_old[color] < pushcolor_dic[color]:
                            self.pushcolor_dic_old[color] = pushcolor_dic[color]
                    else:
                        self.pushcolor_dic_old[color] = pushcolor_dic[color]

                if self.pushcount % self.simple_mode == 0:
                    pushall(self.pushtext_old, self.pushcolor_dic_old, self.push_list)
                    printlog(
                        '[Info] "%s" pushall %s\n%s' % (self.name, str(self.pushcolor_dic_old), self.pushtext_old))
                    writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (
                        self.name, str(self.pushcolor_dic_old), self.pushtext_old))
                    self.pushtext_old = ""
                    # self.pushtext_old = "【%s %s】\n" % (self.__class__.__name__, self.tgt_name)
                    self.pushcolor_dic_old = {}
                else:
                    self.pushtext_old += "\n"

    def punish(self, pushcolor_dic):
        if self.regen != "False":
            time_now = round(time.time())
            regen_amt = int((time_now - self.regen_time) / int(self.regen)) * int(self.regen_amount)
            if regen_amt:
                self.regen_time = time_now
                for color in list(self.pushpunish):
                    if self.pushpunish[color] > regen_amt:
                        self.pushpunish[color] -= regen_amt
                    else:
                        self.pushpunish.pop(color)

        if self.tgt in self.vip_dic:
            for color in self.vip_dic[self.tgt]:
                if color in pushcolor_dic and not color.count("vip"):
                    pushcolor_dic[color] -= self.vip_dic[self.tgt][color]

        for color in self.pushpunish:
            if color in pushcolor_dic and not color.count("vip"):
                pushcolor_dic[color] -= self.pushpunish[color]

        for color in pushcolor_dic:
            if pushcolor_dic[color] > 0 and not color.count("vip"):
                if color in self.pushpunish:
                    self.pushpunish[color] += 1
                else:
                    self.pushpunish[color] = 1
        return pushcolor_dic

    def stop(self):
        self.stop_now = True
        self.ws.close()


# vip=tgt
class LolUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        self.lastgameid = 0
        try:
            getattr(self, "tgt_region")
        except:
            self.tgt_region = "jp"
        try:
            getattr(self, "ingame_onstart")
        except:
            self.ingame_onstart = "True"

    def run(self):    
        while not self.stop_now:
            # 获取用户信息
            user_datadic_new = getloluser(self.tgt, self.tgt_region, self.proxy)
            if isinstance(user_datadic_new, dict):
                if self.is_firstrun:
                    # 首次在线即推送
                    if self.ingame_onstart == "True" and user_datadic_new['user_status'] == 'in_game':
                        pushtext = "【%s %s 当前比赛】\n时间：%s(GMT)\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                            self.__class__.__name__, self.tgt_name, user_datadic_new['user_gametime'], self.tgt_region, self.tgt)
                        self.push(pushtext)
                    
                    self.userdata_dic = user_datadic_new
                    try:
                        self.lastgameid = sorted(user_datadic_new['user_gamedic'], reverse=True)[0]
                    except:
                        pass
                    writelog(self.logpath, '[Info] "%s" getloluser %s firstresult\n%s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    for key in user_datadic_new:
                        # 比赛结果 直接推送
                        if key == 'user_gamedic':
                            for gameid in user_datadic_new['user_gamedic']:
                                if gameid > self.lastgameid:
                                    pushtext = "【%s %s 比赛统计】\n结果：%s\nKDA：%s\n时间：%s(GMT)\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                                        self.__class__.__name__, self.tgt_name,
                                        user_datadic_new['user_gamedic'][gameid]['game_result'],
                                        user_datadic_new['user_gamedic'][gameid]['game_kda'],
                                        user_datadic_new['user_gamedic'][gameid]['game_time'], self.tgt_region,
                                        self.tgt)
                                    self.push(pushtext)
                            try:
                                self.lastgameid = sorted(user_datadic_new['user_gamedic'], reverse=True)[0]
                            except:
                                pass
                        # 当前游戏 整合推送
                        elif key == 'user_status':
                            if user_datadic_new[key] != self.userdata_dic[key]:
                                if user_datadic_new[key] == 'in_game':
                                    pushtext = "【%s %s 比赛开始】\n时间：%s(GMT)\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                                        self.__class__.__name__, self.tgt_name, user_datadic_new['user_gametime'], self.tgt_region, self.tgt)
                                    self.push(pushtext)
                                else:
                                    pushtext = "【%s %s 比赛结束】\n时间：%s(GMT)\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                                        self.__class__.__name__, self.tgt_name, user_datadic_new['user_gametime'], self.tgt_region, self.tgt)
                                    self.push(pushtext)
                            self.userdata_dic[key] = user_datadic_new[key]
                        # 其他 不推送
                        else:
                            self.userdata_dic[key] = user_datadic_new[key]
                writelog(self.logpath, '[Success] "%s" getloluser %s' % (self.name, self.tgt))
                
                # 更新信息 最短间隔120秒
                if round(time.mktime(time.localtime())) - self.userdata_dic['renew_timestamp'] > 120:
                    renew = renewloluser(self.userdata_dic['user_id'], self.tgt_region, self.proxy)
                    if renew:
                        writelog(self.logpath, '[Success] "%s" renewloluser %s' % (self.name, self.userdata_dic['user_id']))
                    else:
                        printlog('[Error] "%s" renewloluser %s' % (self.name, self.userdata_dic['user_id']))
                        writelog(self.logpath, '[Error] "%s" renewloluser %s' % (self.name, self.userdata_dic['user_id']))
            else:
                printlog('[Error] "%s" getloluser %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getloluser %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, pushtext):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt
class SteamUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        try:
            getattr(self, "online_onstart")
        except:
            self.online_onstart = "True"

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            user_datadic_new = getsteamuser(self.tgt, self.cookies, self.proxy)
            if isinstance(user_datadic_new, dict):
                if self.is_firstrun:
                    # 首次在线即推送
                    if self.online_onstart == "True" and 'user_status' in user_datadic_new and (user_datadic_new['user_status'] == 'Currently Online' or user_datadic_new['user_status'] == '当前在线' or user_datadic_new['user_status'] == '現在オンラインです。'):
                        pushtext = "【%s %s 当前在线】\n网址：https://steamcommunity.com/profiles/%s" % (self.__class__.__name__, self.tgt_name, self.tgt)
                        self.push(pushtext)

                    self.userdata_dic = user_datadic_new
                    writelog(self.logpath, '[Info] "%s" getsteamuser %s firstresult\n%s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        if key not in self.userdata_dic:
                            pushtext_body += "新键：%s\n值：%s\n\n" % (key, str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                        elif self.userdata_dic[key] != user_datadic_new[key]:
                            pushtext_body += "键：%s\n原值：%s\n现值：%s\n\n" % (
                                key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                    
                    if pushtext_body:
                        pushtext = "【%s %s 数据改变】\n%s网址：https://steamcommunity.com/profiles/%s" % (
                            self.__class__.__name__, self.tgt_name, pushtext_body, self.tgt)
                        self.push(pushtext)
                writelog(self.logpath, '[Success] "%s" getsteamuser %s' % (self.name, self.tgt))


            else:
                printlog('[Error] "%s" getsteamuser %s' % (self.name, self.tgt))
                writelog(self.logpath, '[Error] "%s" getsteamuser %s' % (self.name, self.tgt))
            time.sleep(self.interval)

    def push(self, pushtext):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


def getyoutubevideodic(user_id, proxy):
    try:
        videolist = {}
        url = "https://www.youtube.com/channel/%s/videos?view=57&flow=grid" % user_id
        response = requests.get(url, stream=True, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'lxml')
            videolist_all = soup.find_all(class_='yt-lockup-content')
            for video in videolist_all:
                try:
                    video_id = video.h3.a["href"].replace('/watch?v=', '')
                    video_title = video.h3.a["title"]
                    if len(video.find(class_="yt-lockup-meta-info").find_all("li")) > 1:
                        video_type, video_status = "视频", "上传"
                        video_timestamp = round(time.time())
                    else:
                        timestamp = video.find(attrs={"data-timestamp": True})
                        if video.find(class_="accessible-description"):
                            if timestamp:
                                video_type, video_status = "首播", "等待"
                                video_timestamp = timestamp["data-timestamp"]
                            else:
                                video_type, video_status = "首播", "开始"
                                video_timestamp = round(time.time())
                        else:
                            if timestamp:
                                video_type, video_status = "直播", "等待"
                                video_timestamp = timestamp["data-timestamp"]
                            else:
                                video_type, video_status = "直播", "开始"
                                video_timestamp = round(time.time())
                    videolist[video_id] = {"video_title": video_title, "video_type": video_type,
                                           "video_status": video_status, "video_timestamp": video_timestamp}
                except:
                    pass
            # 可能为空 可以为空
            return videolist
        else:
            return False
    except:
        return False


def getyoutubevideostatus(video_id, proxy):
    try:
        url = 'https://www.youtube.com/heartbeat?video_id=%s' % video_id
        response = requests.get(url, stream=True, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            try:
                if response.json()["stop_heartbeat"] == "1":
                    video_status = "上传"
                    return video_status
                else:
                    # 测试中stop_heartbeat只在类型为视频的情况下出现且值为1
                    return False
            except:
                if response.json()["status"] == "stop":
                    video_status = "删除"
                elif response.json()["status"] == "ok":
                    video_status = "开始"
                elif "liveStreamability" not in response.json() or "displayEndscreen" in \
                        response.json()["liveStreamability"]["liveStreamabilityRenderer"]:
                    video_status = "结束"
                else:
                    video_status = "等待"
                # 不可能为空 不可以为空
                return video_status
        else:
            return False
    except:
        return False


def getyoutubevideodescription(video_id, proxy):
    try:
        url = 'https://www.youtube.com/watch?v=%s' % video_id
        response = requests.get(url, stream=True, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            video_description = re.findall(r'\\"description\\":{\\"simpleText\\":\\"([^"]*)\\"', response.text)[0]
            video_description = eval('"""{}"""'.format(video_description))
            video_description = eval('"""{}"""'.format(video_description))
            # 可能为空 可以为空 区分空字符串
            return video_description
        else:
            return False
    except:
        return False


def getyoutubechatcontinuation(video_id, proxy):
    try:
        url = 'https://www.youtube.com/live_chat?is_popout=1&v=%s' % video_id
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            continuation = re.findall('"continuation":"([^"]*)"', response.text)[0]
            # 不可能为空 不可以为空
            return continuation
        else:
            return False
    except:
        return False


def getyoutubechatlist(continuation, proxy):
    try:
        chatlist = []
        url = "https://www.youtube.com/live_chat/get_live_chat"
        headers = {
            'authority': 'www.youtube.com',
            'x-youtube-device': 'cbr=Chrome&cbrver=79.0.3945.130&cosver=10.0&cos=Windows',
            'x-youtube-page-label': 'youtube.ytfe.desktop_20200116_5_RC0',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
            'x-youtube-variants-checksum': '781368a49e2fe3e6fdf766601d0a3202',
            'x-youtube-page-cl': '290089588',
            'x-spf-referer': 'https://www.youtube.com/live_chat?continuation=' + continuation,
            'x-youtube-utc-offset': '480',
            'x-youtube-client-name': '1',
            'x-spf-previous': 'https://www.youtube.com/live_chat?continuation=' + continuation,
            'x-youtube-client-version': '2.20200116.05.00',
            'x-youtube-identity-token': 'QUFFLUhqbER4MFo0b1l6b0lNZXJyVk4yc1k3U09YazVPZ3w=',
            'x-youtube-ad-signals': 'dt=1579486488935&flash=0&frm=1&u_tz=480&u_his=3&u_java&u_h=864&u_w=1536&u_ah=824&u_aw=1536&u_cd=24&u_nplug=3&u_nmime=4&bc=31&bih=722&biw=1519&brdim=0%2C0%2C0%2C0%2C1536%2C0%2C1536%2C824%2C400%2C563&vis=2&wgl=true&ca_type=image',
            'accept': '*/*',
            'x-client-data': 'CKO1yQEIirbJAQimtskBCMG2yQEIqZ3KAQi9sMoBCPe0ygEIlrXKAQiZtcoBCOy1ygEI+7vKARirpMoB',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'referer': 'https://www.youtube.com/live_chat?continuation=' + continuation,
        }
        params = (
            ('commandMetadata', '[object Object]'),
            ('continuation', continuation),
            ('hidden', 'false'),
            ('pbj', '1'),
        )
        response = requests.get(url, headers=headers, params=params, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            continuation_new = re.findall('"continuation":"([^"]*)"', response.text)[0]
            chatlist_json = json.loads(response.text)['response']['continuationContents']['liveChatContinuation']
            if 'actions' in chatlist_json:
                for chat in chatlist_json['actions']:
                    try:
                        if 'liveChatTextMessageRenderer' in chat['addChatItemAction']['item']:
                            chat_type = 'message'
                            chat_dic = chat['addChatItemAction']['item']['liveChatTextMessageRenderer']
                        elif 'liveChatPaidMessageRenderer' in chat['addChatItemAction']['item']:
                            chat_type = 'superchat'
                            chat_dic = chat['addChatItemAction']['item']['liveChatPaidMessageRenderer']
                        elif 'liveChatPaidStickerRenderer' in chat['addChatItemAction']['item']:
                            chat_type = 'supersticker'
                            chat_dic = chat['addChatItemAction']['item']['liveChatPaidStickerRenderer']
                        elif 'liveChatMembershipItemRenderer' in chat['addChatItemAction']['item']:
                            chat_type = 'membership'
                            chat_dic = chat['addChatItemAction']['item']['liveChatMembershipItemRenderer']
                        else:
                            chat_type = ''
                            chat_dic = {}

                        if chat_dic:
                            chat_timestamp = round(int(chat_dic['timestampUsec']) / 1000000)
                            chat_username = chat_dic['authorName']['simpleText']
                            chat_userchannel = chat_dic['authorExternalChannelId']
                            chat_text = ''
                            if 'message' in chat_dic:
                                for chat_text_run in chat_dic['message']['runs']:
                                    if 'text' in chat_text_run:
                                        chat_text += chat_text_run['text']
                                    elif 'emoji' in chat_text_run:
                                        chat_text += chat_text_run['emoji']['shortcuts'][0]
                            if 'purchaseAmountText' in chat_dic:
                                chat_type += ' %s' % chat_dic['purchaseAmountText']['simpleText']
                            chatlist.append({"chat_timestamp": chat_timestamp, "chat_username": chat_username,
                                             "chat_userchannel": chat_userchannel, "chat_type": chat_type,
                                             "chat_text": chat_text})
                    except:
                        continue
            # 可能为空 可以为空
            return chatlist, continuation_new
        else:
            return False, continuation
    except:
        return False, continuation


def getyoutubepostdic(user_id, cookies, proxy):
    try:
        postlist = {}
        url = 'https://www.youtube.com/channel/%s/community' % user_id
        headers = {
            'authority': 'www.youtube.com',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        }
        response = requests.get(url, headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            postpage_json = json.loads(re.findall('window\["ytInitialData"\] = (.*);', response.text)[0])
            postlist_json = postpage_json['contents']['twoColumnBrowseResultsRenderer']['tabs'][3]['tabRenderer'][
                'content']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
            for post in postlist_json:
                try:
                    post_info = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                    post_id = post_info['postId']
                    post_time = ''
                    for post_time_run in post_info['publishedTimeText']['runs']:
                        post_time += post_time_run['text']
                    post_text = ''
                    for post_text_run in post_info['contentText']['runs']:
                        post_text += post_text_run['text']
                    postlist[post_id] = {"post_time": post_time, "post_text": post_text}
                except:
                    pass
            # 可能为空 可以为空
            return postlist
        else:
            return False
    except:
        return False


def getyoutubetoken(cookies, proxy):
    try:
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        response = requests.get('https://www.youtube.com', headers=headers, cookies=cookies, proxies=proxy)
        if response.status_code == 200:
            token = re.findall('"XSRF_TOKEN":"([^"]*)"', response.text)[0]
            return token
        else:
            return False
    except:
        return False


# note_id为整数
def getyoutubenotedic(token, cookies, proxy):
    try:
        youtubenotedic = {}
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        params = (
            ('name', 'signalServiceEndpoint'),
            ('signal', 'GET_NOTIFICATIONS_MENU'),
        )
        data = {
            'sej': '{"clickTrackingParams":"CAkQovoBGAIiEwi9tvfcj5vnAhVUQ4UKHYyoBeQ=","commandMetadata":{"webCommandMetadata":{"url":"/service_ajax","sendPost":true,"apiUrl":"/youtubei/v1/notification/get_notification_menu"}},"signalServiceEndpoint":{"signal":"GET_NOTIFICATIONS_MENU","actions":[{"openPopupAction":{"popup":{"multiPageMenuRenderer":{"trackingParams":"CAoQ_6sBIhMIvbb33I-b5wIVVEOFCh2MqAXk","style":"MULTI_PAGE_MENU_STYLE_TYPE_NOTIFICATIONS","showLoadingSpinner":true}},"popupType":"DROPDOWN","beReused":true}}]}}',
            'session_token': token
        }
        response = requests.post('https://www.youtube.com/service_ajax', headers=headers, params=params,
                                 data=data, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            notelist_json = json.loads(response.text)['data']['actions'][0]['openPopupAction']['popup'][
                'multiPageMenuRenderer']['sections'][0]['multiPageMenuNotificationSectionRenderer']['items']
            for note in notelist_json:
                try:
                    if 'notificationRenderer' in note:
                        note_id = note['notificationRenderer']['notificationId']
                        note_text = note['notificationRenderer']['shortMessage']['simpleText']
                        note_time = note['notificationRenderer']['sentTimeText']['simpleText']
                        note_videoid = \
                            note['notificationRenderer']['navigationEndpoint']['commandMetadata']['webCommandMetadata'][
                                'url'].replace("/watch?v=", "")
                        youtubenotedic[int(note_id)] = {"note_text": note_text, "note_time": note_time,
                                                        "note_videoid": note_videoid}
                except:
                    continue
            return youtubenotedic
        else:
            return False
    except:
        return False


def gettwitteruser(user_screenname, cookies, proxy):
    try:
        userdata_dic = {}
        headers = {
            'x-csrf-token': cookies['ct0'],
            'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.117 Safari/537.36',
        }
        params = (
            ('variables', '{"screen_name":"%s","withHighlightedLabel":false}' % user_screenname),
        )
        response = requests.get('https://api.twitter.com/graphql/G6Lk7nZ6eEKd7LBBZw9MYw/UserByScreenName',
                                headers=headers, params=params, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            user_data = response.json()['data']['user']
            userdata_dic["user_id"] = user_data['id']
            userdata_dic["user_restid"] = user_data['rest_id']
            userdata_dic["user_name"] = user_data['legacy']['name']
            userdata_dic["user_screenname"] = user_data['legacy']['screen_name']
            userdata_dic["user_description"] = user_data['legacy']['description']
            userdata_dic["user_entities"] = user_data['legacy']['entities']
            userdata_dic["user_location"] = user_data['legacy']['location']
            userdata_dic["user_profileimage"] = user_data['legacy']['profile_image_url_https']
            userdata_dic["user_bannerimage"] = user_data['legacy']['profile_banner_url']
            userdata_dic["user_twitcount"] = user_data['legacy']['statuses_count']
            userdata_dic["user_mediacount"] = user_data['legacy']['media_count']
            userdata_dic["user_favouritescount"] = user_data['legacy']['favourites_count']
            userdata_dic["user_friendscount"] = user_data['legacy']['friends_count']
            userdata_dic["user_wantretweet"] = user_data['legacy']['want_retweets']
            userdata_dic["user_protected"] = user_data['legacy']['protected']
            userdata_dic["user_candm"] = user_data['legacy']['can_dm']
            userdata_dic["user_canmediatag"] = user_data['legacy']['can_media_tag']
            userdata_dic["user_advertiseraccounttype"] = user_data['legacy']['advertiser_account_type']
            userdata_dic["user_pinnedtweetidsstr"] = user_data['legacy']['pinned_tweet_ids_str']
            userdata_dic["user_profileinterstitialtype"] = user_data['legacy']['profile_interstitial_type']
            userdata_dic["user_verified"] = user_data['legacy']['verified']
            userdata_dic["user_muting"] = user_data['legacy']['muting']
            return userdata_dic
        else:
            return False
    except:
        return False


# tweet_id为整数
def gettwittertweetdic(user_restid, cookies, proxy):
    try:
        tweet_dic = {}
        params = (
            ('include_profile_interstitial_type', '1'),
            ('include_blocking', '1'),
            ('include_blocked_by', '1'),
            ('include_followed_by', '1'),
            ('include_want_retweets', '1'),
            ('include_mute_edge', '1'),
            ('include_can_dm', '1'),
            ('include_can_media_tag', '1'),
            ('skip_status', '1'),
            ('cards_platform', 'Web-12'),
            ('include_cards', '1'),
            ('include_composer_source', 'true'),
            ('include_ext_alt_text', 'true'),
            ('include_reply_count', '1'),
            ('tweet_mode', 'extended'),
            ('include_entities', 'true'),
            ('include_user_entities', 'true'),
            ('include_ext_media_color', 'true'),
            ('include_ext_media_availability', 'true'),
            ('send_error_codes', 'true'),
            ('simple_quoted_tweets', 'true'),
            ('include_tweet_replies', 'true'),
            ('userId', user_restid),
            ('count', '20'),
            ('ext', 'mediaStats,cameraMoment')
        )
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:72.0) Gecko/20100101 Firefox/72.0',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
            'x-twitter-auth-type': 'OAuth2Session',
            'x-twitter-client-language': 'zh-cn',
            'x-twitter-active-user': 'yes',
            'x-csrf-token': cookies['ct0'],
            'Origin': 'https://twitter.com',
            'Connection': 'keep-alive',
            'TE': 'Trailers'
        }
        response = requests.get('https://api.twitter.com/2/timeline/profile/%s.json' % user_restid, headers=headers,
                                params=params, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            tweetlist_dic = response.json()['globalObjects']['tweets']
            for tweet_id in tweetlist_dic:
                try:
                    if tweetlist_dic[tweet_id]['user_id_str'] == user_restid:
                        tweet_timestamp = int(time.mktime(
                            time.strptime(tweetlist_dic[tweet_id]['created_at'], '%a %b %d %H:%M:%S %z %Y')))
                        tweet_text = tweetlist_dic[tweet_id]['full_text']
                        if 'retweeted_status_id_str' in tweetlist_dic[tweet_id]:
                            tweet_type = "转推"
                        elif 'user_mentions' in tweetlist_dic[tweet_id]['entities']:
                            tweet_type = "回复"
                        else:
                            tweet_type = "发布"
                        tweet_media = []
                        if 'media' in tweetlist_dic[tweet_id]['entities']:
                            for media in tweetlist_dic[tweet_id]['entities']['media']:
                                tweet_media.append(media['expanded_url'])
                        tweet_urls = []
                        if 'urls' in tweetlist_dic[tweet_id]['entities']:
                            for url in tweetlist_dic[tweet_id]['entities']['urls']:
                                tweet_urls.append(url['expanded_url'])
                        tweet_mention = ""
                        if 'user_mentions' in tweetlist_dic[tweet_id]['entities']:
                            for user_mention in tweetlist_dic[tweet_id]['entities']['user_mentions']:
                                tweet_mention += "%s\n" % user_mention['screen_name']
                        tweet_dic[int(tweet_id)] = {"tweet_timestamp": tweet_timestamp, "tweet_text": tweet_text,
                                                    "tweet_type": tweet_type, "tweet_media": tweet_media,
                                                    "tweet_urls": tweet_urls, "tweet_mention": tweet_mention}
                except:
                    continue
            return tweet_dic
        else:
            return False
    except:
        return False


# tweet_id为整数
def gettwittersearchdic(qword, cookies, proxy):
    try:
        tweet_dic = {}
        params = (
            ('include_profile_interstitial_type', '1'),
            ('include_blocking', '1'),
            ('include_blocked_by', '1'),
            ('include_followed_by', '1'),
            ('include_want_retweets', '1'),
            ('include_mute_edge', '1'),
            ('include_can_dm', '1'),
            ('include_can_media_tag', '1'),
            ('skip_status', '1'),
            ('cards_platform', 'Web-12'),
            ('include_cards', '1'),
            ('include_composer_source', 'true'),
            ('include_ext_alt_text', 'true'),
            ('include_reply_count', '1'),
            ('tweet_mode', 'extended'),
            ('include_entities', 'true'),
            ('include_user_entities', 'true'),
            ('include_ext_media_color', 'true'),
            ('include_ext_media_availability', 'true'),
            ('send_error_codes', 'true'),
            ('simple_quoted_tweets', 'true'),
            ('tweet_search_mode', 'live'),
            ('count', '20'),
            ('query_source', 'typed_query'),
            ('pc', '1'),
            ('spelling_corrections', '1'),
            ('ext', 'mediaStats,cameraMoment'),
        )
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:72.0) Gecko/20100101 Firefox/72.0',
            'Accept': '*/*',
            'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
            'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
            'x-twitter-auth-type': 'OAuth2Session',
            'x-twitter-client-language': 'zh-cn',
            'x-twitter-active-user': 'yes',
            'x-csrf-token': cookies['ct0'],
            'Origin': 'https://twitter.com',
            'Connection': 'keep-alive',
            'TE': 'Trailers',
        }
        # 推文内容包括#话题标签的文字，filter:links匹配链接图片视频但不匹配#话题标签的链接，%%23相当于#话题标签
        url = 'https://api.twitter.com/2/search/adaptive.json?include_profile_interstitial_type=1&include_blocking=1&include_blocked_by=1&include_followed_by=1&include_want_retweets=1&include_mute_edge=1&include_can_dm=1&include_can_media_tag=1&skip_status=1&cards_platform=Web-12&include_cards=1&include_composer_source=true&include_ext_alt_text=true&include_reply_count=1&tweet_mode=extended&include_entities=true&include_user_entities=true&include_ext_media_color=true&include_ext_media_availability=true&send_error_codes=true&simple_quoted_tweets=true&q=' + qword + '&tweet_search_mode=live&count=20&query_source=typed_query&pc=1&spelling_corrections=1&ext=mediaStats%2CcameraMoment'
        response = requests.get(url, headers=headers, params=params, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            tweetlist_dic = response.json()['globalObjects']['tweets']
            for tweet_id in tweetlist_dic.keys():
                try:
                    tweet_timestamp = int(
                        time.mktime(time.strptime(tweetlist_dic[tweet_id]['created_at'], '%a %b %d %H:%M:%S %z %Y')))
                    tweet_text = tweetlist_dic[tweet_id]['full_text']
                    if 'retweeted_status_id_str' in tweetlist_dic[tweet_id]:
                        tweet_type = "转推"
                    # 不同于用户推特，总是有user_mentions键
                    elif tweetlist_dic[tweet_id]['entities']['user_mentions']:
                        tweet_type = "回复"
                    else:
                        tweet_type = "发布"
                    tweet_media = []
                    if 'media' in tweetlist_dic[tweet_id]['entities']:
                        for media in tweetlist_dic[tweet_id]['entities']['media']:
                            tweet_media.append(media['expanded_url'])
                    tweet_urls = []
                    if 'urls' in tweetlist_dic[tweet_id]['entities']:
                        for url in tweetlist_dic[tweet_id]['entities']['urls']:
                            tweet_urls.append(url['expanded_url'])
                    tweet_mention = ""
                    if 'user_mentions' in tweetlist_dic[tweet_id]['entities']:
                        for user_mention in tweetlist_dic[tweet_id]['entities']['user_mentions']:
                            tweet_mention += "%s\n" % user_mention['screen_name']
                    tweet_dic[int(tweet_id)] = {"tweet_timestamp": tweet_timestamp, "tweet_text": tweet_text,
                                                "tweet_type": tweet_type, "tweet_media": tweet_media,
                                                "tweet_urls": tweet_urls, "tweet_mention": tweet_mention}
                except:
                    continue
            return tweet_dic
        else:
            return False
    except:
        return False


def gettwitcastlive(user_id, proxy):
    try:
        live_dic = {}
        url = 'https://twitcasting.tv/streamchecker.php?u=%s&v=999' % user_id
        response = requests.get(url, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            live = response.text.split("\t")
            live_id = live[0]
            if live_id:
                live_status = "开始"
            else:
                live_status = "结束"
            live_title = unquote(live[7])
            live_dic[live_id] = {"live_status": live_status, "live_title": live_title}
            return live_dic
        else:
            return False
    except:
        return False


def gettwitcastchatlist(live_id, proxy):
    try:
        twitcastchatlist = []
        url = 'https://twitcasting.tv/userajax.php?c=listall&m=%s&n=10&f=0k=0&format=json' % live_id
        response = requests.get(url, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            for i in range(len(response.json()['comments'])):
                try:
                    chat = response.json()['comments'][i]
                    chat_id = chat['id']
                    chat_screenname = chat['author']['screenName']
                    chat_name = chat['author']['name']
                    chat_timestamp = chat['createdAt']
                    chat_text = chat['message']
                    twitcastchatlist.append(
                        {"chat_id": chat_id, "chat_screenname": chat_screenname, "chat_name": chat_name,
                         "chat_timestamp": chat_timestamp, "chat_text": chat_text})
                except:
                    continue
            return twitcastchatlist
        else:
            return False
    except:
        return False


def getfanboxuser(user_id, proxy):
    try:
        userdata_dic = {}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.5",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "DNT": "1",
            "Host": "fanbox.pixiv.net",
            "Origin": "https://www.pixiv.net",
            "Referer": "https://www.pixiv.net/",
            "TE": "Trailers",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:73.0) Gecko/20100101 Firefox/73.0"
        }
        response = requests.get("https://fanbox.pixiv.net/api/creator.get?userId=%s" % user_id, headers=headers,
                                timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            userdata_dic["user_id"] = response.json()["body"]["user"]["userId"]
            userdata_dic["user_name"] = response.json()["body"]["user"]["name"]
            userdata_dic["user_icon"] = response.json()["body"]["user"]["iconUrl"]
            userdata_dic["description"] = response.json()["body"]["description"]
            userdata_dic["coverimage"] = response.json()["body"]["coverImageUrl"]
            userdata_dic["description"] = response.json()["body"]["description"]
            userdata_dic["profilelinks"] = response.json()["body"]["profileLinks"]
            userdata_dic["hasboothshop"] = response.json()["body"]["hasBoothShop"]
            userdata_dic["hasadultcontent"] = response.json()["body"]["hasAdultContent"]
            userdata_dic["isstopped"] = response.json()["body"]["isStopped"]
            userdata_dic["hasadultcontent"] = response.json()["body"]["hasAdultContent"]
            return userdata_dic
        else:
            return False
    except:
        return False


def getfanboxpostdic(user_id, cookies, proxy):
    try:
        post_dic = {}
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.5",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "DNT": "1",
            "Host": "fanbox.pixiv.net",
            "Origin": "https://www.pixiv.net",
            "Referer": "https://www.pixiv.net/",
            "TE": "Trailers",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:73.0) Gecko/20100101 Firefox/73.0"
        }
        response = requests.get("https://fanbox.pixiv.net/api/post.listCreator?userId=%s&limit=10" % user_id,
                                headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            post_list = response.json()['body']['items']
            for post in post_list:
                try:
                    post_id = post['id']
                    post_title = post['title']
                    # python3.8以上才能匹配+08:00形式的时区格式
                    try:
                        post_publishtimestamp = round(
                            time.mktime(time.strptime(post['publishedDatetime'], "%Y-%m-%dT%H:%M:%S%z")))
                    except:
                        post_publishtimestamp = round(time.time())
                    post_type = post['type']
                    post_text = ""
                    if isinstance(post['body'], dict):
                        if 'text' in post['body']:
                            post_text = post['body']['text']
                        elif 'blocks' in post['body']:
                            for block in post['body']['blocks']:
                                post_text += "%s\n" % block['text']
                    post_fee = post['feeRequired']
                    post_dic[post_id] = {"post_title": post_title, "post_publishtimestamp": post_publishtimestamp,
                                         "post_type": post_type, "post_text": post_text, "post_fee": post_fee}
                except:
                    continue
            return post_dic
        else:
            return False
    except:
        return False


def getbilibililivedic(room_id, proxy):
    try:
        live_dic = {}
        response = requests.get("http://api.live.bilibili.com/room/v1/Room/get_info?room_id=%s" % room_id,
                                timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            live = response.json()['data']
            try:
                live_id = round(time.mktime(time.strptime(live['live_time'], '%Y-%m-%d %H:%M:%S')))
            except:
                live_id = ''
            if live['live_status'] == 1:
                live_status = "开始"
            else:
                live_status = "结束"
            live_title = live['title']
            live_dic[live_id] = {'live_status': live_status, 'live_title': live_title}
            return live_dic
        else:
            return False
    except:
        return False


def getbilibilichathostlist(proxy):
    hostlist = []
    try:
        response = requests.get("https://api.live.bilibili.com/room/v1/Danmu/getConf", proxies=proxy)
        if response.status_code == 200:
            hostserver_list = response.json()['data']['host_server_list']
            for hostserver in hostserver_list:
                try:
                    hostlist.append('wss://%s:%s/sub' % (hostserver['host'], hostserver['wss_port']))
                except:
                    continue
    except:
        # 如果返回false会出错
        pass
    return hostlist


def getloluser(user_name, user_region, proxy):
    try:
        userdata_dic = {}
        response = requests.get("https://%s.op.gg/summoner/l=en_US&userName=%s" % (user_region, user_name),
                                timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'lxml')
            # 用户id与时间戳
            userdata_dic["user_id"] = int(soup.find(id="SummonerRefreshButton").get('onclick').split("'")[1])
            userdata_dic["renew_timestamp"] = int(soup.find(class_="LastUpdate").span.get('data-datetime'))
            
            # 比赛结果
            userdata_dic["user_gamedic"] = {}
            for gameitem in soup.find_all(class_='GameItemWrap'):
                game_id = int(gameitem.div.get('data-game-id'))
                game_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(gameitem.div.get('data-game-time'))))
                game_result = gameitem.div.get('data-game-result')
                game_kda = "%s/%s/%s" % (gameitem.find(class_='Kill').text, gameitem.find(class_='Death').text,
                                         gameitem.find(class_='Assist').text)
                userdata_dic["user_gamedic"][game_id] = {"game_time": game_time, "game_result": game_result, "game_kda": game_kda}
        else:
            return False
        
        response = requests.get("https://%s.op.gg/summoner/spectator/l=en_US&userName=%s" % (user_region, user_name),
                                timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'lxml')
            # 当前游戏
            current_gameitem = soup.find(class_="SpectateSummoner")
            if current_gameitem:
                userdata_dic["user_status"] = 'in_game'
                userdata_dic["user_gametime"] = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(current_gameitem.find(class_="Time").span.get("data-datetime"))))
            else:
                userdata_dic["user_status"] = 'not_in_game'
                userdata_dic["user_gametime"] = False
        else:
            return False

        return userdata_dic
    except:
        return False


def renewloluser(user_id, user_region, proxy):
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest"}
        data = "summonerId=%s" % user_id
        response = requests.post("https://%s.op.gg/summoner/ajax/renew.json/" % user_region, headers=headers, data=data, timeout=(3, 7), proxies=proxy)
        if response.status_code == 200:
            return True
        else:
            return False
    except:
        return False


def getsteamuser(user_id, cookies, proxy):
    try:
        userdata_dic = {}
        response = requests.get("https://steamcommunity.com/profiles/%s" % user_id, cookies=cookies, timeout=(3, 7),
                                proxies=proxy)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'lxml')
            if not soup.find(class_="profile_private_info"):
                userdata_dic["user_position"] = soup.find(class_="header_real_name ellipsis").text.strip()
                userdata_dic["user_level"] = soup.find(class_="friendPlayerLevelNum").text.strip()
                userdata_dic["user_status"] = soup.find(class_="profile_in_game_header").text.strip()
                for item_count in soup.find_all(class_="profile_count_link ellipsis"):
                    userdata_dic["user_" + item_count.find(class_="count_link_label").text.strip()] = item_count.find(
                        class_="profile_count_link_total").text.strip()
            return userdata_dic
        else:
            return False
    except:
        return False


# 检测推送力度
def getpushcolordic(text, dic):
    pushcolor_dic = {}
    for word in dic.keys():
        if text.count(word) > 0:
            for color in dic[word]:
                if color in pushcolor_dic:
                    pushcolor_dic[color] += int(dic[word][color])
                else:
                    pushcolor_dic[color] = int(dic[word][color])
    return pushcolor_dic


# 求和推送力度，注意传入subdics必须为tuple类型
def addpushcolordic(*adddics, **kwargs):
    pushcolor_dic = {}
    for adddic in adddics:
        for color in adddic.keys():
            if color in pushcolor_dic:
                pushcolor_dic[color] += adddic[color]
            else:
                pushcolor_dic[color] = adddic[color]
    if "subdics" in kwargs:
        for subdic in kwargs["subdics"]:
            for color in subdic.keys():
                if color in pushcolor_dic:
                    pushcolor_dic[color] -= subdic[color]
                else:
                    pushcolor_dic[color] = -subdic[color]
    return pushcolor_dic


# 查询或修改暂停力度
def checkpause(pause_json, type, id, pausepower=None):
    is_inpause = None
    for i in range(len(pause_json)):
        if pause_json[i]['type'] == type and pause_json[i]['id'] == id:
            is_inpause = i

    if pausepower is not None:
        # 修改
        if is_inpause is not None:
            pause_json[is_inpause]['pausepower'] = pausepower
            return pause_json
        else:
            pause_json.append({'type': type, 'id': id, 'pausepower': pausepower})
            return pause_json
    else:
        # 查询
        if is_inpause is not None:
            return pause_json[is_inpause]['pausepower']
        else:
            return None


# 判断是否推送
def pushall(pushtext, pushcolor_dic, push_list):
    with open('./pause.json', 'r', encoding='utf-8') as f:
        pause_json = json.load(f)
    for push in push_list:
        pausepower = checkpause(pause_json, push['type'], push['id'])
        if pausepower is None:
            pausepower = 0
        for color in push["color_dic"]:
            if color in pushcolor_dic:
                if int(pushcolor_dic[color]) - int(pausepower) >= int(push["color_dic"][color]):
                    push_thread = threading.Thread(args=(pushtext, push), target=pushtoall)
                    push_thread.start()
                    break


# 推送
def pushtoall(pushtext, push):
    # 不论windows还是linux都是127.0.0.1
    if push['type'] == 'qq_user':
        url = 'http://127.0.0.1:%s/send_private_msg?user_id=%s&message=%s' % (
            push['port'], push['id'], quote(str(pushtext)))
        pushtourl(url)
    elif push['type'] == 'qq_group':
        url = 'http://127.0.0.1:%s/send_group_msg?group_id=%s&message=%s' % (
            push['port'], push['id'], quote(str(pushtext)))
        pushtourl(url)
    elif push['type'] == 'miaotixing':
        # 带文字推送可能导致语音和短信提醒失效
        url = 'https://miaotixing.com/trigger?id=%s&text=%s' % (push['id'], quote(str(pushtext)))
        pushtourl(url)
    elif push['type'] == 'miaotixing_simple':
        url = 'https://miaotixing.com/trigger?id=%s' % push['id']
        pushtourl(url)
    elif push['type'] == 'discord':
        url = push['id']
        headers = {"Content-Type": "application/json"}
        data = {"content": pushtext}
        pushtourl(url, headers, json.dumps(data))
    elif push['type'] == 'telegram':
        url = 'https://api.telegram.org/bot%s/sendMessage?chat_id=@%s&text=%s' % (
            push['bot_id'], push['id'], quote(str(pushtext)))
        pushtourl(url)


# 推送到url
def pushtourl(url, headers=None, data=None):
    if data is None:
        data = {}
    if headers is None:
        headers = {}
    for retry in range(1, 5):
        status_code = 'fail'
        try:
            response = requests.post(url, headers=headers, data=data, timeout=(3, 7))
            status_code = response.status_code
        except:
            time.sleep(5)
        finally:
            printlog('[Info] pushtourl：第%s次-结果%s (%s)' % (retry, status_code, url))
            if status_code == 200 or status_code == 204:
                break


def printlog(text):
    logtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    print("[%s] %s" % (logtime, text))
    return


def writelog(logpath, text):
    logtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    with open(logpath, 'a', encoding='utf-8') as log:
        log.write("[%s] %s\n" % (logtime, text))
    return


def waittime(timestamp):
    t = second_to_time(round(int(timestamp) - time.time()))
    return t


def second_to_time(seconds):
    d = int(seconds / 86400)
    seconds = seconds - d * 86400
    h = int(seconds / 3600)
    seconds = seconds - h * 3600
    m = int(seconds / 60)
    s = seconds - m * 60

    if d == 0:
        if h == 0:
            return "%s分%s秒" % (m, s)
        else:
            return "%s小时%s分%s秒" % (h, m, s)
    else:
        return "%s天%s小时%s分%s秒" % (d, h, m, s)


def createmonitor(monitor_name, config):
    monitor_class = config["submonitor_dic"][monitor_name]["class"]
    monitor_target = config["submonitor_dic"][monitor_name]["target"]
    monitor_target_name = config["submonitor_dic"][monitor_name]["target_name"]
    monitor_config = config[config["submonitor_dic"][monitor_name]["config_name"]]
    monitor_config_mod = {}
    for key in config["submonitor_dic"][monitor_name].keys():
        if key != "class" and key != "target" and key != "target_name" and key != "config_name":
            monitor_config_mod[key] = config["submonitor_dic"][monitor_name][key]
    monitor_thread = globals()[monitor_class](monitor_name, monitor_target, monitor_target_name, monitor_config,
                                              **monitor_config_mod)
    monitor_thread.start()
    return monitor_thread


if __name__ == '__main__':
    if not os.path.exists('./log'):
        os.makedirs('./log')
    if not os.path.isfile('./pause.json'):
        with open('./pause.json', 'w', encoding='utf-8') as f:
            json.dump([], f)

    # 读取配置文件
    config_name = input('默认为spider，不用输入json后缀名\n请输入配置文件名称：')
    while True:
        config_path = './%s.json' % (str(config_name))
        if not config_name:
            config_path = './spider.json'
            break
        if os.path.exists(config_path):
            break
        else:
            config_name = input('该配置文件不存在，请重新输入:')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 启动并监视主监视器
    monitor = Monitor("主线程", "main", "main", config)
    monitor.Daemon = True
    monitor.start()
    while True:
        time.sleep(30)
        monitor.checksubmonitor()
