#!/usr/bin/env python
# -*- coding: utf-8 -*-
import nonebot, threading, os
from multiprocessing import Process


def qqbot(host, port):
    nonebot.init()
    nonebot.load_plugins(os.path.join(os.path.dirname(__file__), 'plugins'), 'plugins')
    nonebot.run(host=host, port=port)


if __name__ == '__main__':
    host = input(
        '默认为172.17.0.1，windows应为127.0.0.1，windows docker应为"host.docker.internal"，linux docker应为172.17.0.1，注意coolqhttpapi的ip也应该修改为相应值\n请输入ip:')
    if not host:
        host = '172.17.0.1'
    port = input('默认为8080\n请输入端口:')
    if not port:
        port = '8080'
    portlist = port.split(',')
    thlist = []
    for pt in portlist:
        p = Process(target=qqbot, args=(host, pt,), daemon=True)
        thlist.append(p)

    for th in thlist:
        th.start()

    for th in thlist:
        th.join()
