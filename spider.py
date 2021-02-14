#!/usr/bin/env python
# -*- coding: utf-8 -*-
import copy
import datetime
import json
import os
import re
import struct
import threading
import time
import zlib
from urllib.parse import quote, unquote

import requests
import websocket
from bs4 import BeautifulSoup


# 仅从cfg和cfg_mod中获取参数，不会启动子监视器
class SubMonitor(threading.Thread):
    def __init__(self, name, tgt, tgt_name, cfg, **cfg_mod):
        super().__init__()
        self.name = name
        self.tgt = tgt
        self.tgt_name = tgt_name

        self.interval = 60
        self.timezone = 8
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
        submonitor_config = getattr(self, submonitor_config_name, {"submonitor_dic": {}})
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
        self.standby_chat = getattr(self, "standby_chat", "False")
        # 是否检测在第一次检测时已开启的待机直播间的弹幕
        self.standby_chat_onstart = getattr(self, "standby_chat_onstart", "False")
        # 不记录弹幕
        self.no_chat = getattr(self, "no_chat", "False")
        # 需要推送的情况，其中等待|开始|结束是直播和首播才有的情况，上传是视频才有的情况，删除则都存在
        self.status_push = getattr(self, "status_push", "等待|开始|结束|上传|删除")
        # 推送惩罚恢复间隔
        self.regen = getattr(self, "regen", "False")
        # 每次推送惩罚恢复量
        self.regen_amount = getattr(self, "regen_amount", 1)

    def run(self):
        while not self.stop_now:
            # 更新视频列表
            try:
                videodic_new = getyoutubevideodic(self.tgt, self.cookies, self.proxy)
                for video_id in videodic_new:
                    if video_id not in self.videodic:
                        self.videodic[video_id] = videodic_new[video_id]
                        if not self.is_firstrun or videodic_new[video_id][
                            "video_status"] == "等待" and self.standby_chat_onstart == "True" or videodic_new[video_id][
                            "video_status"] == "开始":
                            self.push(video_id)
                if self.is_firstrun:
                    writelog(self.logpath,
                             '[Info] "%s" getyoutubevideodic %s: %s' % (self.name, self.tgt, videodic_new))
                    self.is_firstrun = False
                writelog(self.logpath, '[Success] "%s" getyoutubevideodic %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" getyoutubevideodic %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getyoutubevideodic %s: %s' % (self.name, self.tgt, e))

            # 更新视频状态
            for video_id in self.videodic:
                if self.videodic[video_id]["video_status"] == "等待" or self.videodic[video_id]["video_status"] == "开始":
                    try:
                        video_status = getyoutubevideostatus(video_id, self.cookies, self.proxy)
                        if self.videodic[video_id]["video_status"] != video_status:
                            self.videodic[video_id]["video_status"] = video_status
                            self.push(video_id)
                        writelog(self.logpath, '[Success] "%s" getyoutubevideostatus %s' % (self.name, video_id))
                    except Exception as e:
                        printlog("[Error] %s getvideostatus %s: %s" % (self.name, video_id, e))
                        writelog(self.logpath, '[Error] "%s" getyoutubevideostatus %s: %s' % (self.name, video_id, e))
            time.sleep(self.interval)

    def push(self, video_id):
        if self.status_push.count(self.videodic[video_id]["video_status"]):
            # 获取视频简介
            try:
                video_description = getyoutubevideodescription(video_id, self.cookies, self.proxy)
                writelog(self.logpath,
                         '[Success] "%s" getyoutubevideodescription %s' % (self.name, video_id))
            except Exception as e:
                printlog('[Error] "%s" getyoutubevideodescription %s: %s' % (self.name, video_id, e))
                writelog(self.logpath, '[Error] "%s" getyoutubevideodescription %s: %s' % (self.name, video_id, e))
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
                    formattime(self.videodic[video_id]["video_timestamp"], self.timezone), video_id)
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
        self.chatpath = './log/%s/%s/%s_chat.txt' % (
            self.__class__.__name__, self.tgt_name, self.name)

        # continuation为字符
        self.continuation = False
        self.key = False
        self.pushpunish = {}
        self.regen_time = 0
        self.tgt_channel = getattr(self, "tgt_channel", "")
        self.regen = getattr(self, "regen", "False")
        self.regen_amount = getattr(self, "regen_amount", 1)

    def run(self):
        while not self.stop_now:
            # 获取continuation
            if not self.continuation:
                try:
                    self.continuation, self.key = getyoutubechatcontinuation(self.tgt, self.proxy)
                    writelog(self.logpath,
                             '[Info] "%s" getyoutubechatcontinuation %s: %s(%s)' % (self.name, self.tgt, self.continuation, self.key))
                    writelog(self.logpath, '[Success] "%s" getyoutubechatcontinuation %s' % (self.name, self.tgt))
                except Exception as e:
                    printlog('[Error] "%s" getyoutubechatcontinuation %s: %s' % (self.name, self.tgt, e))
                    writelog(self.logpath, '[Error] "%s" getyoutubechatcontinuation %s: %s' % (self.name, self.tgt, e))
                    time.sleep(5)
                    continue

            # 获取直播评论列表
            if self.continuation:
                try:
                    chatlist, self.continuation = getyoutubechatlist(self.tgt, self.continuation, self.key, self.proxy)
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
                except Exception as e:
                    printlog('[Error] "%s" getyoutubechatlist %s(%s): %s' % (self.name, self.continuation, self.key, e))
                    writelog(self.logpath, '[Error] "%s" getyoutubechatlist %s(%s): %s' % (self.name, self.continuation, self.key, e))
            time.sleep(self.interval)

    def push(self, chat):
        writelog(self.chatpath, "%s\t%s\t%s\t%s\t%s" % (
            chat["chat_timestamp"], chat["chat_username"], chat["chat_userchannel"], chat["chat_type"],
            chat["chat_text"]))

        pushcolor_vipdic = getpushcolordic(chat["chat_userchannel"], self.vip_dic)
        pushcolor_worddic = getpushcolordic(chat["chat_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushcolor_dic = self.punish(pushcolor_dic)

            pushtext = "【%s %s 直播评论】\n用户：%s\n内容：%s\n类型：%s\n时间：%s\n网址：https://www.youtube.com/watch?v=%s" % (
                self.__class__.__name__, self.tgt_name, chat["chat_username"], chat["chat_text"], chat["chat_type"],
                formattime(chat["chat_timestamp"], self.timezone), self.tgt)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

    def punish(self, pushcolor_dic):
        # 推送惩罚恢复
        if self.regen != "False":
            time_now = getutctimestamp()
            regen_amt = int(int((time_now - self.regen_time) / float(self.regen)) * float(self.regen_amount))
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
            try:
                postdic_new = getyoutubepostdic(self.tgt, self.cookies, self.proxy)
                for post_id in postdic_new:
                    if post_id not in self.postlist:
                        self.postlist.append(post_id)
                        if not self.is_firstrun:
                            self.push(post_id, postdic_new)
                if self.is_firstrun:
                    writelog(self.logpath,
                             '[Info] "%s" getyoutubepostdic %s: %s' % (self.name, self.tgt, postdic_new))
                    self.is_firstrun = False
                writelog(self.logpath, '[Success] "%s" getyoutubepostdic %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" getyoutubepostdic %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getyoutubepostdic %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, post_id, postdic):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_worddic = getpushcolordic(postdic[post_id]["post_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        # 进行推送
        if pushcolor_dic:
            pushtext = "【%s %s 社区帖子】\n内容：%s\n链接：%s\n时间：%s\n网址：https://www.youtube.com/post/%s" % (
                self.__class__.__name__, self.tgt_name, postdic[post_id]["post_text"][0:3000],
                postdic[post_id]["post_link"], postdic[post_id]["post_time"], post_id)
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
                try:
                    self.token = getyoutubetoken(self.cookies, self.proxy)
                    writelog(self.logpath, '[Info] "%s" getyoutubetoken %s: %s' % (self.name, self.tgt, self.token))
                    writelog(self.logpath, '[Success] "%s" getyoutubetoken %s' % (self.name, self.tgt))
                except Exception as e:
                    printlog('[Error] "%s" getyoutubetoken %s: %s' % (self.name, self.tgt, e))
                    writelog(self.logpath, '[Error] "%s" getyoutubetoken %s: %s' % (self.name, self.tgt, e))
                    time.sleep(5)
                    continue

            # 获取订阅通知列表
            if self.token:
                try:
                    notedic_new = getyoutubenotedic(self.token, self.cookies, self.proxy)
                    if self.is_firstrun:
                        if notedic_new:
                            self.note_id_old = sorted(notedic_new, reverse=True)[0]
                        writelog(self.logpath,
                                 '[Info] "%s" getyoutubenotedic %s: %s' % (self.name, self.tgt, notedic_new))
                        self.is_firstrun = False
                    else:
                        for note_id in notedic_new:
                            if note_id > self.note_id_old:
                                self.push(note_id, notedic_new)
                        if notedic_new:
                            self.note_id_old = sorted(notedic_new, reverse=True)[0]
                    writelog(self.logpath, '[Success] "%s" getyoutubenotedic %s' % (self.name, self.tgt))
                except Exception as e:
                    printlog('[Error] "%s" getyoutubenotedic %s: %s' % (self.name, self.tgt, e))
                    writelog(self.logpath, '[Error] "%s" getyoutubenotedic %s: %s' % (self.name, self.tgt, e))
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


# vip=tgt, no_increase="True"/"False", no_repeat="False"/"间隔秒数"
class TwitterUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        # 是否不推送推文和媒体数量的增加
        self.no_increase = getattr(self, "no_increase", "False")
        # 是否不推送短时间内重复的推文和媒体数量
        self.no_repeat = getattr(self, "no_repeat", "False")
        self.statuses_dic = {}
        self.media_dic = {}
        self.favourites_dic = {}

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            try:
                user_datadic_new = gettwitteruser(self.tgt, self.cookies, self.proxy)
                if self.is_firstrun:
                    self.userdata_dic = user_datadic_new
                    writelog(self.logpath,
                             '[Info] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        if key not in self.userdata_dic:
                            pushtext_body += "新键：%s\n值：%s\n" % (key, str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                        elif self.userdata_dic[key] != user_datadic_new[key]:
                            if self.no_repeat != "False" and key == "statuses_count":
                                time_now = getutctimestamp()
                                if user_datadic_new[key] in self.statuses_dic:
                                    if time_now < self.statuses_dic[user_datadic_new[key]] + float(self.no_repeat):
                                        self.userdata_dic[key] = user_datadic_new[key]
                                        self.statuses_dic[user_datadic_new[key]] = time_now
                                        continue
                                self.statuses_dic[user_datadic_new[key]] = time_now
                            if self.no_repeat != "False" and key == "media_count":
                                time_now = getutctimestamp()
                                if user_datadic_new[key] in self.media_dic:
                                    if time_now < self.media_dic[user_datadic_new[key]] + float(self.no_repeat):
                                        self.userdata_dic[key] = user_datadic_new[key]
                                        self.media_dic[user_datadic_new[key]] = time_now
                                        continue
                                self.media_dic[user_datadic_new[key]] = time_now
                            if self.no_repeat != "False" and key == "favourites_count":
                                time_now = getutctimestamp()
                                if user_datadic_new[key] in self.favourites_dic:
                                    if time_now < self.favourites_dic[user_datadic_new[key]] + float(self.no_repeat):
                                        self.userdata_dic[key] = user_datadic_new[key]
                                        self.favourites_dic[user_datadic_new[key]] = time_now
                                        continue
                                self.favourites_dic[user_datadic_new[key]] = time_now
                            if self.no_increase == "True" and (key == "statuses_count" or key == "media_count"):
                                if self.userdata_dic[key] < user_datadic_new[key]:
                                    self.userdata_dic[key] = user_datadic_new[key]
                                    continue
                            pushtext_body += "键：%s\n原值：%s\n现值：%s\n" % (
                                key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]

                    if pushtext_body:
                        self.push(pushtext_body.strip())
                writelog(self.logpath, '[Success] "%s" gettwitteruser %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, pushtext_body):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushtext = "【%s %s 数据改变】\n%s\n时间：%s\n网址：https://twitter.com/%s" % (
                self.__class__.__name__, self.tgt_name, pushtext_body, formattime(None, self.timezone), self.tgt)
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
                try:
                    tgt_dic = gettwitteruser(self.tgt, self.cookies, self.proxy)
                    self.tgt_restid = tgt_dic["rest_id"]
                    writelog(self.logpath, '[Info] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, self.tgt_restid))
                    writelog(self.logpath, '[Success] "%s" gettwitteruser %s' % (self.name, self.tgt))
                except Exception as e:
                    printlog('[Error] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, e))
                    writelog(self.logpath, '[Error] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, e))
                    time.sleep(5)
                    continue

            # 获取推特列表
            if self.tgt_restid:
                try:
                    tweetdic_new = gettwittertweetdic(self.tgt_restid, self.cookies, self.proxy)
                    if self.is_firstrun:
                        if tweetdic_new:
                            self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                        writelog(self.logpath,
                                 '[Info] "%s" gettwittertweetdic %s: %s' % (self.name, self.tgt, tweetdic_new))
                        self.is_firstrun = False
                    else:
                        for tweet_id in tweetdic_new:
                            if tweet_id > self.tweet_id_old:
                                self.push(tweet_id, tweetdic_new)
                        if tweetdic_new:
                            self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                    writelog(self.logpath, '[Success] "%s" gettwittertweetdic %s' % (self.name, self.tgt_restid))
                except Exception as e:
                    printlog('[Error] "%s" gettwittertweetdic %s: %s' % (self.name, self.tgt_restid, e))
                    writelog(self.logpath, '[Error] "%s" gettwittertweetdic %s: %s' % (self.name, self.tgt_restid, e))
            time.sleep(self.interval)

    def push(self, tweet_id, tweetdic):
        # 获取用户推特时大小写不敏感，但检测用户和提及的时候大小写敏感
        pushcolor_vipdic = getpushcolordic("%s\n%s" % (self.tgt, tweetdic[tweet_id]['tweet_mention']),
                                           self.vip_dic)
        pushcolor_worddic = getpushcolordic(tweetdic[tweet_id]['tweet_text'], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushmedia = ""
            if tweetdic[tweet_id]["tweet_media"]:
                pushmedia = "媒体：%s\n" % tweetdic[tweet_id]["tweet_media"]
            pushurl = ""
            if tweetdic[tweet_id]["tweet_urls"]:
                pushurl = "链接：%s\n" % tweetdic[tweet_id]["tweet_urls"]
            pushtext = "【%s %s 推特%s】\n内容：%s\n%s%s时间：%s\n网址：https://twitter.com/%s/status/%s" % (
                self.__class__.__name__, self.tgt_name, tweetdic[tweet_id]["tweet_type"],
                tweetdic[tweet_id]["tweet_text"], pushmedia, pushurl,
                formattime(tweetdic[tweet_id]["tweet_timestamp"], self.timezone), self.tgt, tweet_id)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt+mention, word=text
class TwitterFleet(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.tgt_restid = False
        # fleet_id为整数
        self.fleet_id_old = 0

    def run(self):
        while not self.stop_now:
            # 获取用户restid
            if not self.tgt_restid:
                try:
                    tgt_dic = gettwitteruser(self.tgt, self.cookies, self.proxy)
                    self.tgt_restid = tgt_dic["rest_id"]
                    writelog(self.logpath, '[Info] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, self.tgt_restid))
                    writelog(self.logpath, '[Success] "%s" gettwitteruser %s' % (self.name, self.tgt))
                except Exception as e:
                    printlog('[Error] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, e))
                    writelog(self.logpath, '[Error] "%s" gettwitteruser %s: %s' % (self.name, self.tgt, e))
                    time.sleep(5)
                    continue

            # 获取fleet列表
            if self.tgt_restid:
                try:
                    fleetdic_new = gettwitterfleetdic(self.tgt_restid, self.cookies, self.proxy)
                    if self.is_firstrun:
                        if fleetdic_new:
                            self.fleet_id_old = sorted(fleetdic_new, reverse=True)[0]
                        writelog(self.logpath,
                                 '[Info] "%s" gettwitterfleetdic %s: %s' % (self.name, self.tgt, fleetdic_new))
                        self.is_firstrun = False
                    else:
                        for fleet_id in fleetdic_new:
                            if fleet_id > self.fleet_id_old:
                                self.push(fleet_id, fleetdic_new)
                        if fleetdic_new:
                            self.fleet_id_old = sorted(fleetdic_new, reverse=True)[0]
                    writelog(self.logpath, '[Success] "%s" gettwitterfleetdic %s' % (self.name, self.tgt_restid))
                except Exception as e:
                    printlog('[Error] "%s" gettwitterfleetdic %s: %s' % (self.name, self.tgt_restid, e))
                    writelog(self.logpath, '[Error] "%s" gettwitterfleetdic %s: %s' % (self.name, self.tgt_restid, e))
            time.sleep(self.interval)

    def push(self, fleet_id, fleetdic):
        # 获取用户推特时大小写不敏感，但检测用户和提及的时候大小写敏感
        pushcolor_vipdic = getpushcolordic("%s\n%s" % (self.tgt, fleetdic[fleet_id]['fleet_mention']),
                                           self.vip_dic)
        pushcolor_worddic = getpushcolordic(fleetdic[fleet_id]['fleet_text'], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushtext = "【%s %s fleet发布】\n内容：%s\n时间：%s\n网址：%s" % (
                self.__class__.__name__, self.tgt_name, fleetdic[fleet_id]["fleet_text"], 
                formattime(fleetdic[fleet_id]["fleet_timestamp"], self.timezone), fleetdic[fleet_id]["fleet_urls"])
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
        self.only_live = getattr(self, "only_live", "False")
        # 是否只推送有链接指向youtube直播或视频的推文
        self.only_liveorvideo = getattr(self, "only_liveorvideo", "False")

    def run(self):
        while not self.stop_now:
            # 获取推特列表
            try:
                tweetdic_new = gettwittersearchdic(self.tgt, self.cookies, self.proxy)
                if self.is_firstrun:
                    if tweetdic_new:
                        self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                    writelog(self.logpath,
                             '[Info] "%s" gettwittersearchdic %s: %s' % (self.name, self.tgt, tweetdic_new))
                    self.is_firstrun = False
                else:
                    for tweet_id in tweetdic_new:
                        if tweet_id > self.tweet_id_old:
                            self.push(tweet_id, tweetdic_new)
                    if tweetdic_new:
                        self.tweet_id_old = sorted(tweetdic_new, reverse=True)[0]
                writelog(self.logpath, '[Success] "%s" gettwittersearchdic %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" gettwittersearchdic %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" gettwittersearchdic %s: %s' % (self.name, self.tgt, e))
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
                pushtext = "【%s %s 推特%s】\n内容：%s\n媒体：%s\n链接：%s\n时间：%s\n网址：https://twitter.com/a/status/%s" % (
                    self.__class__.__name__, self.tgt_name, tweetdic[tweet_id]["tweet_type"],
                    tweetdic[tweet_id]["tweet_text"], tweetdic[tweet_id]["tweet_media"],
                    tweetdic[tweet_id]["tweet_urls"], formattime(tweetdic[tweet_id]["tweet_timestamp"], self.timezone),
                    tweet_id)
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
        self.no_chat = getattr(self, "no_chat", "False")
        self.status_push = getattr(self, "status_push", "开始|结束")
        self.regen = getattr(self, "regen", "False")
        self.regen_amount = getattr(self, "regen_amount", 1)

    def run(self):
        while not self.stop_now:
            # 获取直播状态
            try:
                livedic_new = gettwitcastlive(self.tgt, self.proxy)
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
            except Exception as e:
                printlog('[Error] "%s" gettwitcastlive %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" gettwitcastlive %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, live_id):
        if self.status_push.count(self.livedic[live_id]["live_status"]):
            pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
            pushcolor_worddic = getpushcolordic(self.livedic[live_id]["live_title"], self.word_dic)
            pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

            if pushcolor_dic:
                pushtext = "【%s %s 直播%s】\n标题：%s\n时间：%s\n网址：https://twitcasting.tv/%s" % (
                    self.__class__.__name__, self.tgt_name, self.livedic[live_id]["live_status"],
                    self.livedic[live_id]["live_title"], formattime(None, self.timezone), self.tgt)
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
        self.chatpath = './log/%s/%s/%s_chat.txt' % (
            self.__class__.__name__, self.tgt_name, self.name)

        self.chat_id_old = 0
        self.pushpunish = {}
        self.regen_time = 0
        self.tgt_channel = getattr(self, "tgt_channel", "")
        self.regen = getattr(self, "regen", "False")
        self.regen_amount = getattr(self, "regen_amount", 1)

    def run(self):
        while not self.stop_now:
            # 获取直播评论列表
            try:
                chatlist = gettwitcastchatlist(self.tgt, self.proxy)
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
            except Exception as e:
                printlog('[Error] "%s" gettwitcastchatlist %s: %s' % (self.name, self.chat_id_old, e))
                writelog(self.logpath, '[Error] "%s" gettwitcastchatlist %s: %s' % (self.name, self.chat_id_old, e))
            time.sleep(self.interval)

    def push(self, chat):
        writelog(self.chatpath, "%s\t%s\t%s\t%s" % (
            chat["chat_timestamp"], chat["chat_name"], chat["chat_screenname"], chat["chat_text"]))

        pushcolor_vipdic = getpushcolordic(chat["chat_screenname"], self.vip_dic)
        pushcolor_worddic = getpushcolordic(chat["chat_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushcolor_dic = self.punish(pushcolor_dic)

            pushtext = "【%s %s 直播评论】\n用户：%s(%s)\n内容：%s\n时间：%s\n网址：https://twitcasting.tv/%s" % (
                self.__class__.__name__, self.tgt_name, chat["chat_name"], chat["chat_screenname"], chat["chat_text"],
                formattime(chat["chat_timestamp"], self.timezone), self.tgt_channel)
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))

    def punish(self, pushcolor_dic):
        if self.regen != "False":
            time_now = getutctimestamp()
            regen_amt = int(int((time_now - self.regen_time) / float(self.regen)) * float(self.regen_amount))
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
            try:
                user_datadic_new = getfanboxuser(self.tgt, self.proxy)
                if self.is_firstrun:
                    self.userdata_dic = user_datadic_new
                    writelog(self.logpath,
                             '[Info] "%s" getfanboxuser %s: %s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        if key not in self.userdata_dic:
                            pushtext_body += "新键：%s\n值：%s\n" % (key, str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                        elif self.userdata_dic[key] != user_datadic_new[key]:
                            pushtext_body += "键：%s\n原值：%s\n现值：%s\n" % (
                                key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]

                    if pushtext_body:
                        self.push(pushtext_body)
                writelog(self.logpath, '[Success] "%s" getfanboxuser %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" getfanboxuser %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getfanboxuser %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, pushtext_body):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushtext = "【%s %s 数据改变】\n%s\n时间：%s网址：https://%s.fanbox.cc/" % (
                self.__class__.__name__, self.tgt_name, pushtext_body, formattime(None, self.timezone), self.tgt)
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
            try:
                postdic_new = getfanboxpostdic(self.tgt, self.cookies, self.proxy)
                for post_id in postdic_new:
                    if post_id not in self.postlist:
                        self.postlist.append(post_id)
                        if not self.is_firstrun:
                            self.push(post_id, postdic_new)
                if self.is_firstrun:
                    writelog(self.logpath, '[Info] "%s" getfanboxpostdic %s: %s' % (self.name, self.tgt, postdic_new))
                    self.is_firstrun = False
                writelog(self.logpath, '[Success] "%s" getfanboxpostdic %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" getfanboxpostdic %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getfanboxpostdic %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, post_id, postdic):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_worddic = getpushcolordic(postdic[post_id]["post_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushtext = "【%s %s 社区帖子】\n标题：%s\n内容：%s\n类型：%s\n档位：%s\n时间：%s\n网址：https://%s.fanbox.cc/posts/%s" % (
                self.__class__.__name__, self.tgt_name, postdic[post_id]["post_title"],
                postdic[post_id]["post_text"][0:2500],
                postdic[post_id]["post_type"], postdic[post_id]['post_fee'],
                formattime(postdic[post_id]["post_publishtimestamp"], self.timezone), self.tgt, post_id)
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
        self.offline_chat = getattr(self, "offline_chat", "False")
        self.simple_mode = getattr(self, "simple_mode", "False")
        self.no_chat = getattr(self, "no_chat", "False")
        self.status_push = getattr(self, "status_push", "开始|结束")
        self.regen = getattr(self, "regen", "False")
        self.regen_amount = getattr(self, "regen_amount", 1)

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
            try:
                livedic_new = getbilibililivedic(self.tgt, self.proxy)
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
            except Exception as e:
                printlog('[Error] "%s" getbilibililivedic %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getbilibililivedic %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, live_id):
        if self.status_push.count(self.livedic[live_id]["live_status"]):
            pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
            pushcolor_worddic = getpushcolordic(self.livedic[live_id]["live_title"], self.word_dic)
            pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

            if pushcolor_dic:
                pushtext = "【%s %s 直播%s】\n标题：%s\n时间：%s\n网址：https://live.bilibili.com/%s" % (
                    self.__class__.__name__, self.tgt_name, self.livedic[live_id]["live_status"],
                    self.livedic[live_id]["live_title"], formattime(live_id, self.timezone), self.tgt)
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
        self.chatpath = './log/%s/%s/%s_chat.txt' % (
            self.__class__.__name__, self.tgt_name, self.name)

        self.simple_mode = getattr(self, "simple_mode", "False")
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
            self.proxyhost = self.proxy['http'].split(':')[-2].replace('/', '')
            self.proxyport = self.proxy['http'].split(':')[-1]

        self.hostlist = []
        self.hostcount = 1
        self.ws = False
        self.is_linked = False
        self.pushpunish = {}
        self.regen_time = 0
        self.regen = getattr(self, "regen", "False")
        self.regen_amount = getattr(self, "regen_amount", 1)

    def getpacket(self, data, operation):
        """
        packet_length, header_length, protocol_version, operation, sequence_id

        HANDSHAKE=0, HANDSHAKE_REPLY = 1, HEARTBEAT = 2, HEARTBEAT_REPLY = 3, SEND_MSG = 4
        SEND_MSG_REPLY = 5, DISCONNECT_REPLY = 6, AUTH = 7, AUTH_REPLY = 8
        RAW = 9, PROTO_READY = 10, PROTO_FINISH = 11, CHANGE_ROOM = 12
        CHANGE_ROOM_REPLY = 13, REGISTER = 14, REGISTER_REPLY = 15, UNREGISTER = 16, UNREGISTER_REPLY = 17
        """
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
                chat_username = "%s %s %s" % (chat_json['info'][2][1], chat_json['info'][3][1], chat_json['info'][3][0])
                chat_timestamp = float(chat_json['info'][0][4]) / 1000
                # chat_isadmin = dic['info'][2][2] == '1'
                # chat_isvip = dic['info'][2][3] == '1'
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username, 'chat_timestamp': chat_timestamp}
                self.push(chat)
            elif chat_cmd == 'SEND_GIFT':
                chat_type = 'gift %s %s' % (chat_json['data']['giftName'], chat_json['data']['num'])
                chat_text = ''
                chat_userid = str(chat_json['data']['uid'])
                chat_username = "%s %s %s" % (chat_json['data']['uname'], chat_json['data']['medal_info']['medal_name'], chat_json['data']['medal_info']['medal_level'])
                chat_timestamp = float(chat_json['data']['timestamp'])
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username, 'chat_timestamp': chat_timestamp}
                self.push(chat)
            elif chat_cmd == 'SUPER_CHAT_MESSAGE':
                chat_type = 'superchat CN¥%s' % chat_json['data']['price']
                chat_text = chat_json['data']['message']
                chat_userid = str(chat_json['data']['uid'])
                chat_username = "%s %s %s" % (chat_json['data']['uname'], chat_json['data']['medal_info']['medal_name'], chat_json['data']['medal_info']['medal_level'])
                chat_timestamp = float(chat_json['data']['start_time'])
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username, 'chat_timestamp': chat_timestamp}
                self.push(chat)
            elif chat_cmd == 'INTERACT_WORD':
                chat_type = 'enterroom'
                chat_text = ''
                chat_userid = str(chat_json['data']['uid'])
                chat_username = "%s %s %s" % (chat_json['data']['uname'], chat_json['data']['fans_medal']['medal_name'], chat_json['data']['fans_medal']['medal_level'])
                chat_timestamp = float(chat_json['data']['timestamp'])
                chat = {'chat_type': chat_type, 'chat_text': chat_text, 'chat_userid': chat_userid,
                        'chat_username': chat_username, 'chat_timestamp': chat_timestamp}
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
                time.sleep(1)
            else:
                try:
                    self.hostlist = getbilibilichathostlist(self.proxy)
                    self.hostcount = 0
                    writelog(self.logpath,
                             '[Info] "%s" getbilibilichathostlist %s: %s' % (self.name, self.tgt, self.hostlist))
                    writelog(self.logpath, '[Success] "%s" getbilibilichathostlist %s' % (self.name, self.tgt))
                except Exception as e:
                    printlog('[Error] "%s" getbilibilichathostlist %s: %s' % (self.name, self.tgt, e))
                    writelog(self.logpath, '[Error] "%s" getbilibilichathostlist %s: %s' % (self.name, self.tgt, e))
                    time.sleep(5)

    def push(self, chat):
        writelog(self.chatpath, "%s\t%s\t%s\t%s\t%s" % (
            chat["chat_timestamp"], chat["chat_username"], chat["chat_userid"], chat["chat_type"], chat["chat_text"]))

        pushcolor_vipdic = getpushcolordic(chat["chat_userid"], self.vip_dic)
        pushcolor_worddic = getpushcolordic(chat["chat_text"], self.word_dic)
        pushcolor_dic = addpushcolordic(pushcolor_vipdic, pushcolor_worddic)

        if pushcolor_dic:
            pushcolor_dic = self.punish(pushcolor_dic)

            if self.simple_mode == "False":
                pushtext = "【%s %s 直播评论】\n用户：%s(%s)\n内容：%s\n类型：%s\n时间：%s\n网址：https://live.bilibili.com/%s" % (
                    self.__class__.__name__, self.tgt_name, chat["chat_username"], chat["chat_userid"],
                    chat["chat_text"], chat["chat_type"], formattime(chat["chat_timestamp"], self.timezone),
                    self.tgt)
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
            time_now = getutctimestamp()
            regen_amt = int(int((time_now - self.regen_time) / float(self.regen)) * float(self.regen_amount))
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


# vip=tgt, "ingame_onstart"="True"/"False"
class LolUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        self.lastgametimestamp = 0
        self.tgt_region = getattr(self, "tgt_region", "jp")
        self.ingame_onstart = getattr(self, "ingame_onstart", "True")

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            try:
                user_datadic_new = getloluser(self.tgt, self.tgt_region, self.proxy)
                if self.is_firstrun:
                    # 首次在线即推送
                    if self.ingame_onstart == "True" and user_datadic_new['user_status'] == 'in_game':
                        pushtext = "【%s %s 当前比赛】\n时间：%s\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                            self.__class__.__name__, self.tgt_name,
                            formattime(user_datadic_new['user_gametimestamp'], self.timezone), self.tgt_region,
                            self.tgt)
                        self.push(pushtext)

                    self.userdata_dic = user_datadic_new
                    if user_datadic_new['user_gamedic']:
                        self.lastgametimestamp = sorted(user_datadic_new['user_gamedic'], reverse=True)[0]
                    writelog(self.logpath, '[Info] "%s" getloluser %s: %s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    for key in user_datadic_new:
                        # 比赛结果 直接推送
                        if key == 'user_gamedic':
                            for gametimestamp in user_datadic_new['user_gamedic']:
                                if gametimestamp > self.lastgametimestamp:
                                    pushtext = "【%s %s 比赛统计】\n结果：%s\nKDA：%s\n时间：%s\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                                        self.__class__.__name__, self.tgt_name,
                                        user_datadic_new['user_gamedic'][gametimestamp]['game_result'],
                                        user_datadic_new['user_gamedic'][gametimestamp]['game_kda'],
                                        formattime(gametimestamp, self.timezone), self.tgt_region, self.tgt)
                                    self.push(pushtext)
                            if user_datadic_new['user_gamedic']:
                                self.lastgametimestamp = sorted(user_datadic_new['user_gamedic'], reverse=True)[0]
                        # 当前游戏 整合推送
                        elif key == 'user_status':
                            if user_datadic_new[key] != self.userdata_dic[key]:
                                if user_datadic_new[key] == 'in_game':
                                    pushtext = "【%s %s 比赛开始】\n时间：%s\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                                        self.__class__.__name__, self.tgt_name,
                                        formattime(user_datadic_new['user_gametimestamp'], self.timezone),
                                        self.tgt_region, self.tgt)
                                    self.push(pushtext)
                                else:
                                    pushtext = "【%s %s 比赛结束】\n时间：%s\n网址：https://%s.op.gg/summoner/userName=%s&l=en_US" % (
                                        self.__class__.__name__, self.tgt_name, formattime(None, self.timezone),
                                        self.tgt_region, self.tgt)
                                    self.push(pushtext)
                            self.userdata_dic[key] = user_datadic_new[key]
                        # 其他 不推送
                        else:
                            self.userdata_dic[key] = user_datadic_new[key]
                writelog(self.logpath, '[Success] "%s" getloluser %s' % (self.name, self.tgt))

                # 更新信息 最短间隔120秒
                if getutctimestamp() - self.userdata_dic['renew_timestamp'] > 120:
                    try:
                        renewloluser(self.userdata_dic['user_id'], self.tgt_region, self.proxy)
                        writelog(self.logpath,
                                 '[Success] "%s" renewloluser %s' % (self.name, self.userdata_dic['user_id']))
                    except Exception as e:
                        printlog('[Error] "%s" renewloluser %s: %s' % (self.name, self.userdata_dic['user_id'], e))
                        writelog(self.logpath,
                                 '[Error] "%s" renewloluser %s: %s' % (self.name, self.userdata_dic['user_id'], e))
            except Exception as e:
                printlog('[Error] "%s" getloluser %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getloluser %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, pushtext):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt, "online_onstart"="True"/"False"
class SteamUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        self.online_onstart = getattr(self, "online_onstart", "True")

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            try:
                user_datadic_new = getsteamuser(self.tgt, self.cookies, self.proxy)
                if self.is_firstrun:
                    # 首次在线即推送
                    if self.online_onstart == "True" and 'user_status' in user_datadic_new and (
                            user_datadic_new['user_status'] == 'Currently Online' or user_datadic_new[
                        'user_status'] == '当前在线' or user_datadic_new['user_status'] == '現在オンラインです。'):
                        pushtext = "【%s %s 当前在线】\n时间：%s\n网址：https://steamcommunity.com/profiles/%s" % (
                            self.__class__.__name__, self.tgt_name, formattime(None, self.timezone), self.tgt)
                        self.push(pushtext)

                    self.userdata_dic = user_datadic_new
                    writelog(self.logpath, '[Info] "%s" getsteamuser %s: %s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        if key not in self.userdata_dic:
                            pushtext_body += "新键：%s\n值：%s\n" % (key, str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]
                        elif self.userdata_dic[key] != user_datadic_new[key]:
                            pushtext_body += "键：%s\n原值：%s\n现值：%s\n" % (
                                key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                            self.userdata_dic[key] = user_datadic_new[key]

                    if pushtext_body:
                        pushtext = "【%s %s 数据改变】\n%s时间：%s\n网址：https://steamcommunity.com/profiles/%s" % (
                            self.__class__.__name__, self.tgt_name, pushtext_body, formattime(None, self.timezone),
                            self.tgt)
                        self.push(pushtext)
                writelog(self.logpath, '[Success] "%s" getsteamuser %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" getsteamuser %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getsteamuser %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, pushtext):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


# vip=tgt, "online_onstart"="True"/"False"
class OsuUser(SubMonitor):
    def __init__(self, name, tgt, tgt_name, cfg, **config_mod):
        super().__init__(name, tgt, tgt_name, cfg, **config_mod)

        self.logpath = './log/%s/%s.txt' % (self.__class__.__name__, self.name)
        if not os.path.exists('./log/%s' % self.__class__.__name__):
            os.mkdir('./log/%s' % self.__class__.__name__)

        self.is_firstrun = True
        self.userdata_dic = {}
        self.lastgameid = 0
        self.online_onstart = getattr(self, "online_onstart", "True")

    def run(self):
        while not self.stop_now:
            # 获取用户信息
            try:
                user_datadic_new = getosuuser(self.tgt, self.cookies, self.proxy)
                if self.is_firstrun:
                    # 首次在线即推送
                    if self.online_onstart == "True" and 'is_online' in user_datadic_new and user_datadic_new[
                        'is_online'] == 'true':
                        pushtext = "【%s %s 当前在线】\n时间：%s\n网址：https://osu.ppy.sh/users/%s" % (
                            self.__class__.__name__, self.tgt_name, formattime(None, self.timezone), self.tgt)
                        self.push(pushtext)

                    self.userdata_dic = user_datadic_new
                    if user_datadic_new['user_gamedic']:
                        self.lastgameid = sorted(user_datadic_new['user_gamedic'], reverse=True)[0]
                    writelog(self.logpath, '[Info] "%s" getosuuser %s: %s' % (self.name, self.tgt, user_datadic_new))
                    self.is_firstrun = False
                else:
                    pushtext_body = ""
                    for key in user_datadic_new:
                        # 比赛结果 直接推送
                        if key == 'user_gamedic':
                            for gameid in user_datadic_new['user_gamedic']:
                                if gameid > self.lastgameid:
                                    pushtext = "【%s %s 比赛统计】\n类型：%s\n结果：%s\n时间：%s\n网址：https://osu.ppy.sh/users/%s" % (
                                        self.__class__.__name__, self.tgt_name,
                                        user_datadic_new['user_gamedic'][gameid]['game_type'],
                                        user_datadic_new['user_gamedic'][gameid]['game_result'],
                                        formattime(user_datadic_new['user_gamedic'][gameid]['game_timestamp'],
                                                   self.timezone), self.tgt)
                                    self.push(pushtext)
                            if user_datadic_new['user_gamedic']:
                                self.lastgameid = sorted(user_datadic_new['user_gamedic'], reverse=True)[0]
                        # 其他 整合推送
                        else:
                            if key not in self.userdata_dic:
                                pushtext_body += "新键：%s\n值：%s\n" % (key, str(user_datadic_new[key]))
                                self.userdata_dic[key] = user_datadic_new[key]
                            elif self.userdata_dic[key] != user_datadic_new[key]:
                                pushtext_body += "键：%s\n原值：%s\n现值：%s\n" % (
                                    key, str(self.userdata_dic[key]), str(user_datadic_new[key]))
                                self.userdata_dic[key] = user_datadic_new[key]

                    if pushtext_body:
                        pushtext = "【%s %s 数据改变】\n%s\n时间：%s网址：https://osu.ppy.sh/users/%s" % (
                            self.__class__.__name__, self.tgt_name, pushtext_body, formattime(None, self.timezone),
                            self.tgt)
                        self.push(pushtext)
                writelog(self.logpath, '[Success] "%s" getosuuser %s' % (self.name, self.tgt))
            except Exception as e:
                printlog('[Error] "%s" getosuuser %s: %s' % (self.name, self.tgt, e))
                writelog(self.logpath, '[Error] "%s" getosuuser %s: %s' % (self.name, self.tgt, e))
            time.sleep(self.interval)

    def push(self, pushtext):
        pushcolor_vipdic = getpushcolordic(self.tgt, self.vip_dic)
        pushcolor_dic = pushcolor_vipdic

        if pushcolor_dic:
            pushall(pushtext, pushcolor_dic, self.push_list)
            printlog('[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))
            writelog(self.logpath, '[Info] "%s" pushall %s\n%s' % (self.name, str(pushcolor_dic), pushtext))


def getyoutubevideodic(user_id, cookies, proxy):
    try:
        videodic = {}
        url = "https://www.youtube.com/channel/%s/videos?view=57&flow=grid" % user_id
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
            'accept-language': 'zh-CN'}
        response = requests.get(url, headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)

        if response.text.count('window["ytInitialData"]'):
            videolist_json = json.loads(re.findall('window\["ytInitialData"\] = (.*);', response.text)[0])
        else:
            videolist_json = json.loads(re.findall('>var ytInitialData = (.*?);</script>', response.text)[0])
        videolist = []

        def __search(key, json):
            for k in json:
                if k == key:
                    videolist.append(json[k])
                elif isinstance(json[k], dict):
                    __search(key, json[k])
                elif isinstance(json[k], list):
                    for item in json[k]:
                        if isinstance(item, dict):
                            __search(key, item)
            return

        __search('gridVideoRenderer', videolist_json)
        __search('videoRenderer', videolist_json)
        for video_json in videolist:
            video_id = video_json['videoId']
            video_title = ''
            if 'simpleText' in video_json['title']:
                video_title = video_json['title']['simpleText']
            elif 'runs' in video_json['title']:
                for video_title_text in video_json['title']['runs']:
                    video_title += video_title_text['text']

            types = video_json['thumbnailOverlays'][0]['thumbnailOverlayTimeStatusRenderer']['text']['accessibility']['accessibilityData']['label']
            if types == "PREMIERE" or types == "首播" or types == 'プレミア':
                video_type = "首播"
            elif types == "LIVE" or types == "直播" or types == 'ライブ':
                video_type = "直播"
            else:
                video_type = "视频"

            status = video_json['thumbnailOverlays'][0]['thumbnailOverlayTimeStatusRenderer']['style']
            if status == "UPCOMING":
                video_status = "等待"
                video_timestamp = float(video_json['upcomingEventData']['startTime'])
            elif status == "LIVE":
                video_status = "开始"
                video_timestamp = getutctimestamp()
            else:
                # status == "DEFAULT"
                video_status = "上传"
                video_timestamp = getutctimestamp()

            videodic[video_id] = {"video_title": video_title, "video_type": video_type,
                                  "video_status": video_status, "video_timestamp": video_timestamp}
        return videodic
    except Exception as e:
        raise e


def getyoutubevideostatus(video_id, cookies, proxy):
    try:
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        params = (
            ('alt', 'json'),
            ('key', 'AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8'),
        )
        data = {
            "videoId": video_id,
            "context": {
                "client": {
                    "utcOffsetMinutes": "0",
                    "deviceId": "Chrome",
                    "deviceMake": "www",
                    "deviceModel": "www",
                    "browserName": "Chrome",
                    "browserVersion": "83.0.4103.61",
                    "osName": "Windows",
                    "osVersion": "10.0",
                    "clientName": "WEB",
                    "clientVersion": "2.20200529.02.01",
                },
                # "activePlayers": [{"playerContextParams":"Q0FFU0FnZ0M="}] #固定值，暂时不需要
            },
            # "cpn":"1111111111111111", #可变值，暂时不需要
            "heartbeatToken": "",
            "heartbeatRequestParams": {"heartbeatChecks": ["HEARTBEAT_CHECK_TYPE_LIVE_STREAM_STATUS"]}
        }

        response = requests.post("https://www.youtube.com/youtubei/v1/player/heartbeat", headers=headers, params=params,
                                 data=str(data), cookies=cookies, timeout=(3, 7), proxies=proxy)

        if "stopHeartbeat" in response.json():
            video_status = "上传"
        else:
            if response.json()["playabilityStatus"]["status"] == "UNPLAYABLE":
                video_status = "删除"
            elif response.json()["playabilityStatus"]["status"] == "OK":
                video_status = "开始"
            elif "liveStreamability" not in response.json()["playabilityStatus"] or "displayEndscreen" in \
                    response.json()["playabilityStatus"]["liveStreamability"]["liveStreamabilityRenderer"]:
                video_status = "结束"
            else:
                video_status = "等待"
        return video_status
    except Exception as e:
        raise e


'''
def __getyoutubevideostatus(video_id, cookies, proxy):
    """
    删除:
    
    视频上传:"isLiveContent":false
    
    直播等待:"isLiveContent":true,"isLiveNow":false
    直播开始:"isLiveContent":true,"isLiveNow":true
    直播结束:"isLiveContent":true,"isLiveNow":false,"endTimestamp":
    
    首播等待:"isLiveContent":false,"isLiveNow":false
    首播开始:"isLiveContent":false,"isLiveNow":true
    首播结束:"isLiveContent":false,"isLiveNow":false,"endTimestamp":
    """
    try:
        url = 'https://www.youtube.com/watch?v=%s' % video_id
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        response = requests.get(url, headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)
        soup = BeautifulSoup(response.text, 'lxml')
        script = eval('"""{}"""'.format(soup.find(string=re.compile(r'\\"isLiveContent\\":'))))
        if script == "None":
            video_status = "删除"
        elif script.count('"isLiveNow":'):
            if script.count('"endTimestamp":'):
                video_status = "结束"
            elif script.count('"isLiveNow":true'):
                video_status = "开始"
            else:
                video_status = "等待"
        else:
            video_status = "上传"
        return video_status
    except Exception as e:
        raise e
'''


def getyoutubevideodescription(video_id, cookies, proxy):
    try:
        url = 'https://www.youtube.com/watch?v=%s' % video_id
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        response = requests.get(url, headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.text.count(r'\"description\":{\"simpleText\":\"'):
            video_description = re.findall(r'\\"description\\":{\\"simpleText\\":\\"(.*?)\\"}', response.text)[0]
        else:
            video_description = re.findall(r'\"description\":{\"simpleText\":\"(.*?)\"}', response.text)[0]
        video_description = eval('"""{}"""'.format(video_description))
        video_description = eval('"""{}"""'.format(video_description))
        return video_description
    except Exception as e:
        raise e


def getyoutubechatcontinuation(video_id, proxy):
    try:
        url = 'https://www.youtube.com/live_chat?is_popout=1&v=%s' % video_id
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=(3, 7), proxies=proxy)
        continuation = re.findall('"continuation":"([^"]*)"', response.text)[0]
        key = re.findall('"INNERTUBE_API_KEY":"([^"]*)"', response.text)[0]
        if continuation:
            return continuation, key
        else:
            raise Exception("Invalid continuation")
    except Exception as e:
        raise e


def getyoutubechatlist(video_id, continuation, key, proxy):
    try:
        chatlist = []
        url = "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key=%s" % key
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
            'x-youtube-client-name': '1',
            'x-youtube-client-version': '2.20210128.02.00',
            'referer': 'https://www.youtube.com/live_chat?is_popout=1&v=%s' % video_id
        }
        data = {
            "context":{
                "client":{
                    "userAgent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
                    "clientName":"WEB",
                    "clientVersion":"2.20210128.02.00",
                    "originalUrl":"https://www.youtube.com/live_chat?is_popout=1&v=%s" % video_id,
                    "mainAppWebInfo":{
                        "graftUrl":"https://www.youtube.com/live_chat?is_popout=1&v=%s" % video_id
                    }
                }
            },
            "continuation":continuation}
        response = requests.post(url, headers=headers, json=data, timeout=(3, 7), proxies=proxy)
        continuation_new = re.findall('"continuation": "([^"]*)"', response.text)[0]
        chatlist_json = json.loads(response.text)['continuationContents']['liveChatContinuation']
        if 'actions' in chatlist_json:
            for chat in chatlist_json['actions']:
                if 'addChatItemAction' in chat:
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
                        chat_timestamp = float(chat_dic['timestampUsec']) / 1000000
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
                        chatlist.append(
                            {"chat_timestamp": chat_timestamp, "chat_username": chat_username,
                             "chat_userchannel": chat_userchannel, "chat_type": chat_type,
                             "chat_text": chat_text})
        return chatlist, continuation_new
    except Exception as e:
        raise e


def getyoutubepostdic(user_id, cookies, proxy):
    try:
        postlist = {}
        url = 'https://www.youtube.com/channel/%s/community' % user_id
        headers = {
            'authority': 'www.youtube.com',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36',
        }
        response = requests.get(url, headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)
        if response.text.count('window["ytInitialData"]'):
            postpage_json = json.loads(re.findall('window\["ytInitialData"\] = (.*);', response.text)[0])
        else:
            postpage_json = json.loads(re.findall('>var ytInitialData = (.*?);</script>', response.text)[0])
        postlist_json = postpage_json['contents']['twoColumnBrowseResultsRenderer']['tabs'][3]['tabRenderer'][
            'content']['sectionListRenderer']['contents'][0]['itemSectionRenderer']['contents']
        for post in postlist_json:
            if 'backstagePostThreadRenderer' in post:
                post_info = post['backstagePostThreadRenderer']['post']['backstagePostRenderer']
                post_id = post_info['postId']
                post_time = ''
                for post_time_run in post_info['publishedTimeText']['runs']:
                    post_time += post_time_run['text']
                post_text = ''
                for post_text_run in post_info['contentText']['runs']:
                    post_text += post_text_run['text']
                post_link = ''
                if 'backstageAttachment' in post_info:
                    if 'videoRenderer' in post_info['backstageAttachment']:
                        if 'videoId' in post_info['backstageAttachment']['videoRenderer']:
                            post_link = 'https://www.youtube.com/watch?v=%s' % post_info['backstageAttachment']['videoRenderer']['videoId']
                postlist[post_id] = {"post_time": post_time, "post_text": post_text, "post_link": post_link}
        return postlist
    except Exception as e:
        raise e


def getyoutubetoken(cookies, proxy):
    try:
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/79.0.3945.130 Safari/537.36'}
        response = requests.get('https://www.youtube.com', headers=headers, cookies=cookies, proxies=proxy)
        token = re.findall('"XSRF_TOKEN":"([^"]*)"', response.text)[0]
        if token:
            return token
        else:
            raise Exception("Invalid token")
    except Exception as e:
        raise e


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
        notesec_json = \
            json.loads(response.text)['data']['actions'][0]['openPopupAction']['popup']['multiPageMenuRenderer'][
                'sections'][0]
        if 'multiPageMenuNotificationSectionRenderer' in notesec_json:
            for note in notesec_json['multiPageMenuNotificationSectionRenderer']['items']:
                if 'notificationRenderer' in note:
                    note_id = int(note['notificationRenderer']['notificationId'])
                    note_text = note['notificationRenderer']['shortMessage']['simpleText']
                    note_time = note['notificationRenderer']['sentTimeText']['simpleText']
                    note_videoid = \
                        note['notificationRenderer']['navigationEndpoint']['commandMetadata']['webCommandMetadata'][
                            'url'].replace("/watch?v=", "")
                    youtubenotedic[note_id] = {"note_text": note_text, "note_time": note_time,
                                               "note_videoid": note_videoid}
        return youtubenotedic
    except Exception as e:
        raise e


def gettwitteruser(user_screenname, cookies, proxy):
    try:
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

        user_data = response.json()['data']['user']
        userdata_dic = user_data
        for key in user_data['legacy']:
            userdata_dic[key] = user_data['legacy'][key]
        userdata_dic.pop('legacy')

        userdata_dic.pop('followers_count')
        userdata_dic.pop('normal_followers_count')
        userdata_dic.pop('listed_count')
        userdata_dic.pop('notifications')
        userdata_dic.pop('muting')
        userdata_dic.pop('blocked_by')
        userdata_dic.pop('blocking')
        userdata_dic.pop('follow_request_sent')
        userdata_dic.pop('followed_by')
        userdata_dic.pop('following')

        return userdata_dic
    except Exception as e:
        raise e


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

        if 'globalObjects' in response.json():
            tweetlist_dic = response.json()['globalObjects']['tweets']
            for tweet_id in tweetlist_dic:
                if tweetlist_dic[tweet_id]['user_id_str'] == user_restid:
                    tweet_timestamp = phrasetimestamp(tweetlist_dic[tweet_id]['created_at'], '%a %b %d %H:%M:%S %z %Y')
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
        return tweet_dic
    except Exception as e:
        raise e


def gettwitterfleetdic(user_restid, cookies, proxy):
    try:
        fleet_dic = {}
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
        response = requests.get('https://api.twitter.com/fleets/v1/user_fleets?user_id=%s' % user_restid, headers=headers, 
                                cookies=cookies, timeout=(3, 7), proxies=proxy)

        for fleetthread in response.json()['fleet_threads']:
            for fleet in fleetthread['fleets']:
                fleet_timestamp = phrasetimestamp(fleet['created_at'].split('.')[0], "%Y-%m-%dT%H:%M:%S")
                fleet_text = ""
                fleet_mention = ""
                if 'media_bounding_boxes' in fleet:
                    for fleet_box in fleet['media_bounding_boxes']:
                        if fleet_box['entity']['type'] == 'text':
                            fleet_text += "%s\n" % fleet_box['entity']['value']
                        elif fleet_box['entity']['type'] == 'mention':
                            fleet_mention += "%s\n" % fleet_box['entity']['value'].replace('@', '')
                fleet_url = fleet['media_entity']['media_url_https']
                fleet_dic[int(fleet['fleet_id'].split('-')[1])] = {"fleet_timestamp": fleet_timestamp, "fleet_text": fleet_text.strip(), "fleet_mention": fleet_mention, "fleet_urls": fleet_url}
        return fleet_dic
    except Exception as e:
        raise e


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

        if 'globalObjects' in response.json():
            tweetlist_dic = response.json()['globalObjects']['tweets']
            for tweet_id in tweetlist_dic.keys():
                tweet_timestamp = phrasetimestamp(tweetlist_dic[tweet_id]['created_at'], '%a %b %d %H:%M:%S %z %Y')
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
        return tweet_dic
    except Exception as e:
        raise e


def gettwitcastlive(user_id, proxy):
    try:
        live_dic = {}
        url = 'https://twitcasting.tv/streamchecker.php?u=%s&v=999' % user_id
        response = requests.get(url, timeout=(3, 7), proxies=proxy)
        live = response.text.split("\t")
        live_id = live[0]
        if live_id:
            live_status = "开始"
        else:
            live_status = "结束"
        live_title = unquote(live[7])
        live_dic[live_id] = {"live_status": live_status, "live_title": live_title}
        return live_dic
    except Exception as e:
        raise e


def gettwitcastchatlist(live_id, proxy):
    try:
        twitcastchatlist = []
        url = 'https://twitcasting.tv/userajax.php?c=listall&m=%s&n=10&f=0k=0&format=json' % live_id
        response = requests.get(url, timeout=(3, 7), proxies=proxy)
        for i in range(len(response.json()['comments'])):
            chat = response.json()['comments'][i]
            chat_id = chat['id']
            chat_screenname = chat['author']['screenName']
            chat_name = chat['author']['name']
            chat_timestamp = float(chat['createdAt']) / 1000
            chat_text = chat['message']
            twitcastchatlist.append(
                {"chat_id": chat_id, "chat_screenname": chat_screenname, "chat_name": chat_name,
                 "chat_timestamp": chat_timestamp, "chat_text": chat_text})
        return twitcastchatlist
    except Exception as e:
        raise e


def getfanboxuser(user_id, proxy):
    try:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.5",
            "Cache-Control": "max-age=0",
            "Connection": "keep-alive",
            "DNT": "1",
            "Host": "api.fanbox.cc",
            "Origin": "https://%s.fanbox.cc" % user_id,
            "Referer": "https://%s.fanbox.cc/" % user_id,
            "TE": "Trailers",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:73.0) Gecko/20100101 Firefox/73.0"
        }
        response = requests.get("https://api.fanbox.cc/creator.get?creatorId=%s" % user_id, headers=headers,
                                timeout=(3, 7), proxies=proxy)

        user_data = response.json()["body"]
        userdata_dic = user_data
        for key in user_data['user']:
            userdata_dic[key] = user_data['user'][key]
        userdata_dic.pop('user')

        userdata_dic.pop('isFollowed')
        userdata_dic.pop('isSupported')

        return userdata_dic
    except Exception as e:
        raise e


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
            "Host": "api.fanbox.cc",
            "Origin": "https://%s.fanbox.cc" % user_id,
            "Referer": "https://%s.fanbox.cc/" % user_id,
            "TE": "Trailers",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:73.0) Gecko/20100101 Firefox/73.0"
        }
        response = requests.get("https://api.fanbox.cc/post.listCreator?creatorId=%s&limit=10" % user_id,
                                headers=headers, cookies=cookies, timeout=(3, 7), proxies=proxy)

        post_list = response.json()['body']['items']
        for post in post_list:
            post_id = post['id']
            post_title = post['title']
            # python3.6无法识别+00:00格式，只能识别+0000格式
            try:
                post_publishtimestamp = phrasetimestamp(post['publishedDatetime'], "%Y-%m-%dT%H:%M:%S%z")
            except:
                post_publishtimestamp = phrasetimestamp(post['publishedDatetime'].replace(':', ''), "%Y-%m-%dT%H%M%S%z")
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
        return post_dic
    except Exception as e:
        raise e


def getbilibililivedic(room_id, proxy):
    try:
        live_dic = {}
        response = requests.get("http://api.live.bilibili.com/room/v1/Room/get_info?room_id=%s" % room_id,
                                timeout=(3, 7), proxies=proxy)
        live = response.json()['data']
        try:
            live_id = phrasetimestamp(live['live_time'] + " +0800", '%Y-%m-%d %H:%M:%S %z')
        except:
            live_id = ''
        if live['live_status'] == 1:
            live_status = "开始"
        else:
            live_status = "结束"
        live_title = live['title']
        live_dic[live_id] = {'live_status': live_status, 'live_title': live_title}
        return live_dic
    except Exception as e:
        raise e


def getbilibilichathostlist(proxy):
    hostlist = []
    try:
        response = requests.get("https://api.live.bilibili.com/room/v1/Danmu/getConf", proxies=proxy)
        hostserver_list = response.json()['data']['host_server_list']
        for hostserver in hostserver_list:
            hostlist.append('wss://%s:%s/sub' % (hostserver['host'], hostserver['wss_port']))
        if hostlist:
            return hostlist
        else:
            raise Exception("Invalid hostlist")
    except Exception as e:
        raise e


def getloluser(user_name, user_region, proxy):
    try:
        userdata_dic = {}
        response = requests.get("https://%s.op.gg/summoner/l=en_US&userName=%s" % (user_region, user_name),
                                timeout=(3, 7), proxies=proxy)
        soup = BeautifulSoup(response.text, 'lxml')
        # 用户id与时间戳
        userdata_dic["user_id"] = int(soup.find(id="SummonerRefreshButton").get('onclick').split("'")[1])
        userdata_dic["renew_timestamp"] = float(soup.find(class_="LastUpdate").span.get('data-datetime'))
        # 比赛结果
        userdata_dic["user_gamedic"] = {}
        for gameitem in soup.find_all(class_='GameItemWrap'):
            game_timestamp = float(gameitem.div.get('data-game-time'))
            game_id = int(gameitem.div.get('data-game-id'))
            game_result = gameitem.div.get('data-game-result')
            game_kda = "%s/%s/%s" % (gameitem.find(class_='Kill').text, gameitem.find(class_='Death').text,
                                     gameitem.find(class_='Assist').text)
            userdata_dic["user_gamedic"][game_timestamp] = {"game_id": game_id, "game_result": game_result,
                                                            "game_kda": game_kda}

        response = requests.get("https://%s.op.gg/summoner/spectator/l=en_US&userName=%s" % (user_region, user_name),
                                timeout=(3, 7), proxies=proxy)
        soup = BeautifulSoup(response.text, 'lxml')
        # 当前游戏
        current_gameitem = soup.find(class_="SpectateSummoner")
        if current_gameitem:
            userdata_dic["user_status"] = 'in_game'
            userdata_dic["user_gametimestamp"] = float(current_gameitem.find(class_="Time").span.get("data-datetime"))
        else:
            userdata_dic["user_status"] = 'not_in_game'
            userdata_dic["user_gametimestamp"] = False

        return userdata_dic
    except Exception as e:
        raise e


def renewloluser(user_id, user_region, proxy):
    try:
        headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "X-Requested-With": "XMLHttpRequest"}
        data = "summonerId=%s" % user_id
        response = requests.post("https://%s.op.gg/summoner/ajax/renew.json/" % user_region, headers=headers, data=data,
                                 timeout=(3, 7), proxies=proxy)
        if response.status_code != 200:
            raise Exception("Refresh failed")
    except Exception as e:
        raise e


def getsteamuser(user_id, cookies, proxy):
    try:
        userdata_dic = {}
        response = requests.get("https://steamcommunity.com/profiles/%s" % user_id, cookies=cookies, timeout=(3, 7),
                                proxies=proxy)
        soup = BeautifulSoup(response.text, 'lxml')
        if not soup.find(class_="profile_private_info"):
            userdata_dic["user_position"] = soup.find(class_="header_real_name ellipsis").text.strip()
            userdata_dic["user_level"] = soup.find(class_="friendPlayerLevelNum").text.strip()
            userdata_dic["user_status"] = soup.find(class_="profile_in_game_header").text.strip()
            userdata_dic["user_avatar"] = soup.find(class_="playerAvatarAutoSizeInner").img['src']
            for item_count in soup.find_all(class_="profile_count_link ellipsis"):
                userdata_dic["user_" + item_count.find(class_="count_link_label").text.strip()] = item_count.find(
                    class_="profile_count_link_total").text.strip()
        return userdata_dic
    except Exception as e:
        raise e


def getosuuser(user_id, cookies, proxy):
    try:
        response = requests.get('https://osu.ppy.sh/users/%s' % user_id, cookies=cookies, timeout=(3, 7), proxies=proxy)
        soup = BeautifulSoup(response.text, 'lxml')
        user_data = json.loads(soup.find(attrs={'id': 'json-user', 'type': 'application/json'}).text)
        userdata_dic = user_data
        for key in user_data['statistics']:
            userdata_dic[key] = user_data['statistics'][key]
        userdata_dic.pop('statistics')

        userdata_dic.pop('follower_count')
        userdata_dic.pop('rank')
        userdata_dic.pop('rankHistory')
        userdata_dic.pop('rank_history')
        userdata_dic.pop('pp_rank')
        userdata_dic.pop('last_visit')

        # 比赛结果
        userdata_dic["user_gamedic"] = {}
        gamelist = json.loads(soup.find(attrs={'id': 'json-extras', 'type': 'application/json'}).text)['recentActivity']
        for gameitem in gamelist:
            game_id = gameitem['id']
            # python3.6无法识别+00:00格式，只能识别+0000格式
            try:
                game_timestamp = phrasetimestamp(gameitem['createdAt'], "%Y-%m-%dT%H:%M:%S%z")
            except:
                game_timestamp = phrasetimestamp(gameitem['createdAt'].replace(':', ''), "%Y-%m-%dT%H%M%S%z")
            game_type = gameitem['type']
            try:
                game_result = "%s - %s(%s) - %s(https://osu.ppy.sh/%s)" % (
                    gameitem['mode'], gameitem['scoreRank'], gameitem['rank'], gameitem['beatmap']['title'],
                    gameitem['beatmap']['url'])
            except:
                game_result = ''
            userdata_dic["user_gamedic"][game_id] = {"game_timestamp": game_timestamp, "game_type": game_type,
                                                     "game_result": game_result}
        return userdata_dic
    except Exception as e:
        raise e


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
    if 'proxy' not in push:
        push['proxy'] = ''
    # 不论windows还是linux都是127.0.0.1
    if push['type'] == 'qq_user':
        if 'ip' not in push:
            push['ip'] = '127.0.0.1'
        url = 'http://%s:%s/send_private_msg?user_id=%s&message=%s' % (
            push['ip'], push['port'], push['id'], quote(str(pushtext)))
        pushtourl('GET', url, push['proxy'])
    elif push['type'] == 'qq_group':
        if 'ip' not in push:
            push['ip'] = '127.0.0.1'
        url = 'http://%s:%s/send_group_msg?group_id=%s&message=%s' % (
            push['ip'], push['port'], push['id'], quote(str(pushtext)))
        pushtourl('GET', url, push['proxy'])
    elif push['type'] == 'miaotixing':
        # 带文字推送可能导致语音和短信提醒失效
        url = 'https://miaotixing.com/trigger?id=%s&text=%s' % (push['id'], quote(str(pushtext)))
        pushtourl('POST', url, push['proxy'])
    elif push['type'] == 'miaotixing_simple':
        url = 'https://miaotixing.com/trigger?id=%s' % push['id']
        pushtourl('POST', url, push['proxy'])
    elif push['type'] == 'discord':
        url = push['id']
        headers = {"Content-Type": "application/json"}
        data = {"content": pushtext}
        pushtourl('POST', url, push['proxy'], headers, json.dumps(data))
    elif push['type'] == 'telegram':
        url = 'https://api.telegram.org/bot%s/sendMessage?chat_id=%s&text=%s' % (
            push['bot_id'], push['id'], quote(str(pushtext)))
        pushtourl('POST', url, push['proxy'])


# 推送到url
def pushtourl(method, url, proxy, headers=None, data=None):
    if data is None:
        data = {}
    if headers is None:
        headers = {}
    for retry in range(1, 5):
        status_code = 'fail'
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=(10, 30), proxies=proxy)
            elif method == 'POST':
                response = requests.post(url, headers=headers, data=data, timeout=(10, 30), proxies=proxy)
            status_code = response.status_code
        except:
            time.sleep(5)
        finally:
            printlog('[Info] pushtourl：第%s次-结果%s (%s proxy:%s)' % (retry, status_code, url, proxy))
            if status_code == 200 or status_code == 204:
                break


def phrasetimestamp(text, timeformat):
    return datetime.datetime.strptime(text, timeformat).timestamp()


def getutctimestamp():
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).timestamp()


# log文件时间时区由hs默认值指定
def formattime(ts=None, hs=8):
    if ts:
        return datetime.datetime.utcfromtimestamp(float(ts)).replace(tzinfo=datetime.timezone.utc).astimezone(
            tz=datetime.timezone(datetime.timedelta(hours=hs))).strftime("%Y-%m-%d %H:%M:%S %Z")
    else:
        return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).astimezone(
            tz=datetime.timezone(datetime.timedelta(hours=hs))).strftime("%Y-%m-%d %H:%M:%S %Z")


def printlog(text):
    print("[%s] %s" % (formattime(), text))


def writelog(logpath, text):
    with open(logpath, 'a', encoding='utf-8') as log:
        log.write("[%s] %s\n" % (formattime(), text))


'''
def waittime(timestamp):
    return second_to_time(int(float(timestamp)) - formattime())


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
'''


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
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
