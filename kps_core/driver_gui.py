"""Small independent dialogs; invoke production actions through the host facade."""
import json
from .driver_stm32 import discover


def _window(app, api, title):
    tk, ttk = api.tk, api.ttk
    win = tk.Toplevel(app.root)
    win.title(title)
    app._size_dialog(win, 960, 720)
    win.minsize(760, 540)
    win.transient(app.root)
    win.configure(background=app.colors['bg'])
    frame = ttk.Frame(win, padding=16, style='Surface.TFrame')
    frame.pack(fill='both', expand=True)
    win.grab_set()
    return win, frame


def open_reference_manager(app, api):
    tr, tk, ttk = api._gt, api.tk, api.ttk
    try:
        proj = app._project()
        rows = api.reference_inventory(proj)
    except Exception as exc:
        api.messagebox.showerror(tr('无法打开', 'Cannot open'), str(exc), parent=app.root)
        return
    win, frame = _window(app, api, tr('移除文件与路径', 'Remove files and paths'))
    ttk.Label(frame, text=tr('仅移除 Keil 引用，不删除磁盘源码', 'Remove Keil references, never source files'),
              style='TaskTitle.TLabel').pack(anchor='w')
    ttk.Label(frame, text=tr('Target：', 'Targets: ')+', '.join(proj.target_names()), style='Body.TLabel').pack(anchor='w', pady=6)
    ttk.Label(frame, text=tr('勾选具体条目。组件管理的引用需到“安全与恢复”卸载；移除 Include 可能影响其他源码编译。',
        'Check exact entries. Managed components require Safety uninstall; removing includes can break remaining sources.'),
        wraplength=860, style='Muted.TLabel').pack(anchor='w')
    search = tk.StringVar()
    ttk.Label(frame, text=tr('筛选（路径 / Target / 分组）', 'Filter (path / target / group)'), style='Body.TLabel').pack(anchor='w', pady=(12, 3))
    ttk.Entry(frame, textvariable=search).pack(fill='x')
    box = ttk.Frame(frame, style='Surface.TFrame')
    box.pack(fill='both', expand=True, pady=10)
    tree = ttk.Treeview(box, columns=('check','target','kind','path','group'), show='headings', selectmode='browse', style='Modern.Treeview')
    for key, title, width in [('check','✓',50),('target','Target',180),
                             ('kind',tr('类型','Type'),100),('path',tr('路径','Path'),420),('group',tr('分组','Group'),130)]:
        tree.heading(key, text=title)
        tree.column(key, width=width, stretch=(key=='path'))
    vertical = ttk.Scrollbar(box, orient='vertical', command=tree.yview)
    horizontal = ttk.Scrollbar(box, orient='horizontal', command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0,column=0,sticky='nsew'); vertical.grid(row=0,column=1,sticky='ns')
    horizontal.grid(row=1,column=0,sticky='ew')
    box.rowconfigure(0,weight=1); box.columnconfigure(0,weight=1)
    checked = set()
    count = tk.StringVar()
    def refresh(*_):
        tree.delete(*tree.get_children())
        term=search.get().casefold()
        for i,r in enumerate(rows):
            if term and term not in (' '.join(str(v) for v in r.values())).casefold():
                continue
            tree.insert('', 'end', iid=str(i), values=('☑' if i in checked else '☐',r['target'],r['kind'],r['path'],r['group']))
        count.set(tr('已勾选 %d 项（含筛选隐藏项）','%d checked (including filtered entries)') % len(checked))
    def toggle(event):
        iid = tree.focus() if event.keysym=='space' else tree.identify_row(event.y)
        if iid:
            i=int(iid)
            if i in checked: checked.remove(i)
            else: checked.add(i)
            tree.set(iid,'check','☑' if i in checked else '☐')
            count.set(tr('已勾选 %d 项（含筛选隐藏项）','%d checked (including filtered entries)') % len(checked))
        if event.keysym=='space': return 'break'
    tree.bind('<Button-1>',toggle); tree.bind('<space>',toggle)
    search.trace_add('write',refresh)
    refresh()
    ttk.Label(frame,textvariable=count,style='Body.TLabel').pack(anchor='w')
    bar=ttk.Frame(frame,style='Surface.TFrame'); bar.pack(fill='x',pady=(10,0))
    def select_visible():
        checked.update(int(i) for i in tree.get_children()); refresh()
    def clear():
        checked.clear(); refresh()
    def apply():
        if not checked:
            api.messagebox.showinfo(tr('未选择','Nothing selected'),tr('请先勾选引用。','Check references first.'),parent=win)
            return
        selected=[rows[i] for i in sorted(checked)]
        win.destroy()
        try:
            app._background_call(api.remove_project_references, proj, selected,
                                 preview_callback=app.confirm_diff_preview)
            app.status_var.set(tr('引用移除检查完成，详情见日志。','Reference removal checked; see log.'))
        except Exception as exc:
            api.messagebox.showerror(tr('移除停止','Removal stopped'),str(exc),parent=app.root)
    ttk.Button(bar,text=tr('勾选筛选结果','Check filtered'),command=select_visible).pack(side='left')
    ttk.Button(bar,text=tr('全部取消','Uncheck all'),command=clear).pack(side='left',padx=6)
    ttk.Button(bar,text=tr('关闭','Close'),command=win.destroy).pack(side='right')
    ttk.Button(bar,text=tr('预览移除…','Preview removal…'),command=apply,style='Primary.TButton').pack(side='right',padx=6)


def open_driver_generator(app, api):
    tr, tk, ttk = api._gt, api.tk, api.ttk
    try:
        proj=app._project()
        config_path=api.project_content_root(proj) / 'KPS' / 'DeviceDrivers' / 'driver-pack.json'
        existing=json.loads(config_path.read_text(encoding='utf-8')) if config_path.is_file() else {}
        found=discover(proj,api.read_source_text)
    except Exception as exc:
        api.messagebox.showerror(tr('无法打开','Cannot open'),str(exc),parent=app.root)
        return
    win,frame=_window(app,api,tr('器件驱动生成器','Device driver generator'))
    ttk.Label(frame,text=tr('选择器件与通信方式','Choose devices and transports'),style='TaskTitle.TLabel').pack(anchor='w')
    ttk.Label(frame,text='Target: '+', '.join(proj.target_names()),style='Body.TLabel').pack(anchor='w',pady=5)
    ttk.Label(frame,text=tr('生成到工程 KPS/DeviceDrivers，保留已有文件。auto 自动填板级 API，generic 手填 TODO；不改 main.c。',
        'Output: KPS/DeviceDrivers. Preserve existing files. auto fills board APIs; generic needs TODOs. No main.c edits.'),wraplength=860,style='Muted.TLabel').pack(anchor='w')
    chosen={}
    devices=ttk.Frame(frame,style='Surface.TFrame'); devices.pack(fill='x')
    for index,(key,(title,detail,_url)) in enumerate(api.DRIVER_CATALOG.items()):
        var=tk.BooleanVar(value=key in existing.get('drivers', api.DRIVER_CATALOG)); chosen[key]=var
        ttk.Checkbutton(devices,text=title,variable=var,
                        style='Modern.TCheckbutton').grid(row=index//2,column=index%2,sticky='w',padx=(0,30),pady=5)
    modes=ttk.Frame(frame,style='Surface.TFrame'); modes.pack(fill='x',pady=10)
    i2c=tk.StringVar(value=existing.get('i2c','hardware')); spi=tk.StringVar(value=existing.get('spi','hardware'))
    ttk.Label(modes,text='I2C',style='Body.TLabel').grid(row=0,column=0,padx=5)
    ttk.Combobox(modes,textvariable=i2c,values=api.I2C_MODES,state='readonly',width=14).grid(row=0,column=1)
    ttk.Label(modes,text='SPI',style='Body.TLabel').grid(row=0,column=2,padx=(25,5))
    ttk.Combobox(modes,textvariable=spi,values=api.SPI_MODES,state='readonly',width=14).grid(row=0,column=3)
    board=existing.get('board',{})
    port=tk.StringVar(value='auto' if board or (not existing and found['profile'] in ('hal','spl')) else 'generic')
    ttk.Label(modes,text=tr('端口','Port'),style='Body.TLabel').grid(row=1,column=0,pady=8)
    ttk.Combobox(modes,textvariable=port,values=('auto','generic'),state='readonly',width=14).grid(row=1,column=1)
    ttk.Label(modes,text=found['device']+' / '+found['profile'],style='Body.TLabel').grid(row=1,column=2,columnspan=2,sticky='w',padx=20)
    fields={}
    bindings=ttk.Frame(frame,style='Surface.TFrame'); bindings.pack(fill='x')
    for row,(key,label) in enumerate((('i2c_instance','I2C'),('spi_instance','SPI'),('cs','CS'),('scl','SCL'),('sda','SDA'))):
        kind=key.split('_')[0]
        choices=found.get(kind,[]) if key.endswith('instance') else []
        value=board.get(kind if key.endswith('instance') else key) or (choices[0] if len(choices)==1 else '')
        fields[key]=tk.StringVar(value=value)
        col=(row%3)*2; line=row//3
        ttk.Label(bindings,text=label,style='Body.TLabel').grid(row=line,column=col,sticky='w',padx=5,pady=5)
        ttk.Combobox(bindings,textvariable=fields[key],values=choices,width=15).grid(row=line,column=col+1,padx=5)
    ttk.Label(frame,text=tr('auto：F1/F4 HAL/SPL 自动填 API；选择已有总线，CS/SCL/SDA 填 PB6 等实际引脚。\n'
        '不猜接线；auto 暂不支持 DMA。generic 保留手工接口。只配置明确选定的 CS / 软件 I2C GPIO。',
        'auto: F1/F4 HAL/SPL APIs; choose existing buses and real GPIOs (e.g. PB6).\n'
        'No guessed wiring or automatic DMA. generic keeps manual hooks. Only selected CS/soft-I2C pins are configured.'),
        style='Muted.TLabel',wraplength=860).pack(anchor='w',pady=6)
    ttk.Label(frame,text=tr(
        'hardware：硬件阻塞，适合先验证接线。software：软件 I2C，需开漏 GPIO / 微秒延时。\n'
        'dma：等待完成的 DMA 框架，需填写启动 / 等待 / 中止与持久缓冲区；短报文不一定更快。\n'
        '未做对应器件的实板验收。所有读写需由用户显式调用，不会自动擦除或格式化。',
        'hardware: blocking hardware, best for initial bring-up. software: open-drain GPIO + microsecond delay.\n'
        'dma: completion-waiting framework; fill start/wait/abort and persistent buffers. Short transfers may be slower.\n'
        'No physical device acceptance. Explicit calls only; no automatic erase/format.'),wraplength=860,style='Muted.TLabel').pack(anchor='w',pady=8)
    bar=ttk.Frame(frame,style='Surface.TFrame'); bar.pack(side='bottom',fill='x',pady=10)
    def generate():
        selected=[k for k,v in chosen.items() if v.get()]
        if not selected:
            api.messagebox.showinfo(tr('未选择','Nothing selected'),tr('至少选择一种器件。','Select at least one device.'),parent=win)
            return
        opts=api.SimpleNamespace(drivers=selected,driver_i2c=i2c.get(),driver_spi=spi.get(),
            driver_port=port.get(),**{'driver_'+key:var.get().strip() for key,var in fields.items()},
            yes=False,dry_run=False,preview_callback=app.confirm_diff_preview)
        win.destroy()
        try:
            changed=app._background_call(api.run_tasks,proj,['device_drivers'],opts)
            if getattr(proj,'_planning_failed',False):
                raise api.ToolError(tr('生成规划失败，详情见日志；未写入。','Planning failed; see log. Nothing written.'))
            if changed:
                api.messagebox.showinfo(tr('生成完成','Generated'),tr('请阅读 KPS/DeviceDrivers/DRIVER_GUIDE.md。auto 完成总线初始化后调用 kps_stm32_init；generic 先填写 TODO。',
                    'Read KPS/DeviceDrivers/DRIVER_GUIDE.md. auto: initialize buses then call kps_stm32_init. generic: fill TODOs first.'),parent=app.root)
        except Exception as exc:
            api.messagebox.showerror(tr('生成停止','Generation stopped'),str(exc),parent=app.root)
    ttk.Button(bar,text=tr('关闭','Close'),command=win.destroy).pack(side='right')
    ttk.Button(bar,text=tr('预览生成…','Preview generation…'),command=generate,style='Primary.TButton').pack(side='right',padx=8)
