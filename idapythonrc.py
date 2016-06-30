# C:\Users\[USERNAME]\AppData\Roaming\Hex-Rays\IDA Pro\idapythonrc.py
from idaapi import *
import idc
import zlib
import traceback
import webbrowser
import datetime
import re
from codemap import codemap

__version__ = '2.0'

codemap = codemap.Codemap()


def IDA_State():
    res = ''
    if get_root_filename() is None:
        return 'empty'

    try:
        # x86/x64
        a = idc.GetRegValue('esp')
        res = 'running'
    except:    
        try:
            # arm
            a = idc.GetRegValue('r0')
            res = 'running'
        except:
            res = 'static'
    return res


# batch function break point script - daehee
def Functions(start=None, end=None):
    if not start:
        start = cvar.inf.minEA
    if not end:
        end = cvar.inf.maxEA
    chunk = get_fchunk(start)
    if not chunk:
        chunk = get_next_fchunk(start)
    while chunk and chunk.startEA < end and (chunk.flags & FUNC_TAIL) != 0:
        chunk = get_next_fchunk(chunk.startEA)
    func = chunk
    while func and func.startEA < end:
        yield (func)
        func = get_next_func(func.startEA)


def FuncItems(start):
    func = get_func(start)
    if not func:
        return
    fii = func_item_iterator_t()
    ok = fii.set(func)
    while ok:
        yield fii.current()
        ok = fii.next_code()

'''
Returns a list of module objects with name,size,base and the rebase_to
'''
def Modules():
    mod = idaapi.module_info_t()
    result = idaapi.get_first_module(mod)
    while result:
        yield idaapi.object_t(name=mod.name, size=mod.size, base=mod.base, rebase_to=mod.rebase_to)
        result = idaapi.get_next_module(mod)


# print slows IDA down
class IDAHook(DBG_Hooks):
    global codemap

    def dbg_process_exit(self, pid, tid, ea, code):
        codemap.db_insert()
        codemap.init_codemap()
        print("Process exited pid=%d tid=%d ea=0x%x code=%d" %
              (pid, tid, ea, code))

    def dbg_bpt(self, tid, ea):
        if codemap.pause is True:
            return 0    # stop visualizing

        codemap.set_data()
        codemap.db_insert_queue()
        if ea not in codemap.bpts:
            continue_process()                  # continue
        return 0    # no warning

def hook_ida():
    global debughook
    # Remove an existing debug hook
    try:
        if debughook:
            print("Removing previous hook ...")
            debughook.unhook()
    except:
        pass
    # Install the debug hook
    debughook = IDAHook()
    debughook.hook()
    debughook.steps = 0

'''
- SetRangeTrace - 
Get address range from user and setup trace point to all instruction in that range.
'''
def SetRangeTrace():
    if IDA_State() == 'empty':
        print 'no program loaded'
        return
    start_addr = AskStr('', 'Start Addr? (e.g. 0x8000) : ')
    end_addr = AskStr('', 'End Addr? (e.g. 0xC000) : ')

    start_addr = int(start_addr.replace('0x', ''), 16)
    end_addr = int(end_addr.replace('0x', ''), 16)

    for e in Heads(start_addr, end_addr+1):
        ToggleTrace(def_ea=e)
            
    return


'''
- SetFunctionTrace - 
put cursor inside the IDA-recognized function then call this. 
bp will be set to all instructions of function
'''
def SetFunctionTrace():
    if IDA_State() == 'empty':
        print 'no program loaded'
        return
    ea = ScreenEA()
    target = 0
    for e in Functions():
        if e.startEA <= ea and ea <= e.endEA:
            target = e.startEA

    if target != 0:
        for e in FuncItems(target):
            ToggleTrace(def_ea=e)
    else:
        Warning('put cursor in the function body')


'''
- Start Trace -
setup all bp's you want(maybe using GETBPLIST or SetFunctionBP or manually)
then execute this script in order to create dygraph trace.
'''
def StartTracing():
    global codemap
    if codemap.start is False and IDA_State() != 'running':
        print 'IDA debugger not running'
        return

    if codemap.start is True:
        codemap.pause = not codemap.pause
        print 'Codemap Paused? : ', codemap.pause
        if codemap.pause is False:    # resume tracing
            continue_process()
        else:
            codemap.db_insert()
            suspend_process()
        return

    # set current uid. if there is existing codemap instance, save it to prev_uids
    if codemap.uid != None:
        codemap.prev_uids.append( codemap.uid )

    codemap.uid = datetime.datetime.fromtimestamp(
        time.time()).strftime('%Y%m%d%H%M%S')
    
    
    codemap.init_arch()
    hook_ida()

    print 'hook ida done.'
    print 'homedir : ', codemap.homedir
    print 'making table...'
    # initialize sqlite3 db
    print codemap.db_create()

    # set default SQL
    if codemap.arch.name == 'x86':
        codemap.query = "select eip from trace{0}".format(codemap.uid)
    if codemap.arch.name == 'x64':
        codemap.query = "select rip from trace{0}".format(codemap.uid)
    
    print 'start HTTP server'
    # start HTTP server
    codemap.start_webserver()

    # fire up chrome!
    result = 'http://{0}:{1}/{2}'.format(codemap.server,
                                         codemap.port,
                                         codemap.uid)
    webbrowser.open(result)
    print 'start tracing...'
    codemap.start = True
    continue_process()


'''
- SaveModuleBP -
open a dll file with IDA and execute this script after IDA analysis is done.
the function offset information of dll will be saved to file inside Codemap directory
'''
def SaveModuleBP():
    global codemap
    try:
        modname = AskStr('', 'module name : ')
        bpo = ''
        for e in Functions():
            func = e.startEA
            length = e.endEA - e.startEA
            if length < codemap.func_min_size:
                continue
            offset = func - get_imagebase()
            bpo += str(offset) + '\n'
        print 'bp offset generation complete! ' + str(len(bpo))
        payload = bpo
        with open(codemap.homedir + modname + '.bpo', 'wb') as f:
            f.write(zlib.compress(payload))
    except:
        traceback.print_exc(file=sys.stdout)
        
'''
- LoadModuleBP -
while debugging the target app, put cursor somewhere inside the target module code. 
execute the script and bp will be set for all functions specified in .bpo file
'''
def LoadModuleBP():
    global codemap
    try:
        # get current cursor
        cur = get_screen_ea()
        baseaddr = 0
        modname = ''
        # what module is my cursor pointing?
        for i in Modules():
            if cur > i.base and cur < i.base + i.size:
                modname = i.name.split('\x00')[0]
                modname = modname.split('\\')[-1:][0]
                baseaddr = i.base

        codemap.base = baseaddr         # this is needed.
        modname = AskStr('', 'module name : ')
        payload = ''
        with open(codemap.homedir + modname + '.bpo', 'rb') as f:
            payload = zlib.decompress(f.read())
        bps = payload.split()
        code = bytearray()
        for bp in bps:
            ToggleTrace(def_ea=(baseaddr+int(bp)))
        print 'setting breakpoints...'
        # set bp!
        #exec(str(code))
    except:
        traceback.print_exc(file=sys.stdout)

def SetModuleTrace():
    global codemap
    if codemap.start is False and IDA_State() is 'static':
        SaveModuleBP()
    if codemap.start is False and IDA_State() is 'running':
        LoadModuleBP()

def ListenCodemap():
    global codemap
    codemap.start_websocketserver()
    print "Listning to codemap connection..."
    
def CmtRefresh(ea):
    global codemap
    bp = 'bp' if ea in codemap.bpts else ''
    tr = 'tr' if ea in codemap.tpts else ''
    new_prefix = '*{}* '.format('{} {}'.format(bp, tr).lstrip().rstrip())
    new_prefix = new_prefix if new_prefix != '** ' else ''
    
    cmt = idc.Comment(ea)
    cmt = cmt if cmt is not None else ''
    
    if re.match(r'^(\*[bptr\ ]+\*\ )', cmt):
        cmt = re.sub(r'^(\*[bptr\ ]+\*\ )', new_prefix, cmt)
    else:
        cmt = new_prefix + cmt
        
    idc.MakeComm(ea, cmt)
    
    return
    
def ToggleBP():
    global codemap
    ea = ScreenEA()
    if ea == 0xffffffff:
        print 'invalid ea value : {}'.format(ea)
        return
    
    chkbpt = idc.CheckBpt(ea)
    if chkbpt == 1:
        if ea in codemap.bpts and ea in codemap.tpts:
            codemap.bpts.remove(ea)
        elif ea in codemap.bpts and ea not in codemap.tpts:
            codemap.bpts.remove(ea)
            del_bpt(ea)
        elif ea not in codemap.bpts and ea in codemap.tpts:
            codemap.bpts.append(ea)
        else:
            print "Not Expected State. Check Design Once More"
            
    elif chkbpt == -1:
        if ea not in codemap.bpts and ea not in codemap.tpts:
            codemap.bpts.append(ea)
            add_bpt(ea, 0, BPT_SOFT)
        else:
            print "Not Expected State. Check Design Once More"
    
    else:
        print "Not Expected value : CheckBpt ({})".format(chkbpt)
        return
        
    CmtRefresh(ea)
    
    return
    
def ToggleTrace(def_ea=None):
    global codemap
    if def_ea is None:
        ea = ScreenEA()
    else:
        ea = def_ea
    
    if ea == 0xffffffff:
        print 'invalid ea value : {}'.format(ea)
        return
    
    chkbpt = idc.CheckBpt(ea)
    if chkbpt == 1:
        if ea in codemap.bpts and ea in codemap.tpts:
            codemap.tpts.remove(ea)
        elif ea in codemap.bpts and ea not in codemap.tpts:
            codemap.tpts.append(ea)
        elif ea not in codemap.bpts and ea in codemap.tpts:
            codemap.tpts.remove(ea)
            del_bpt(ea)
        else:
            print "Not Expected State. Check Design Once More"
            
    elif chkbpt == -1:
        if ea not in codemap.bpts and ea not in codemap.tpts:
            codemap.tpts.append(ea)
            add_bpt(ea, 0, BPT_SOFT)
        else:
            print "Not Expected State. Check Design Once More"
    
    else:
        print "Not Expected value : CheckBpt ({})".format(chkbpt)
        return
        
    CmtRefresh(ea)

    return


CompileLine('static key_1() { RunPythonStatement("StartTracing()"); }')
CompileLine('static key_2() { RunPythonStatement("SetFunctionTrace()"); }')
CompileLine('static key_3() { RunPythonStatement("SetRangeTrace()"); }')
CompileLine('static key_4() { RunPythonStatement("SetModuleTrace()"); }')
CompileLine('static key_5() { RunPythonStatement("ListenCodemap()"); }')
CompileLine('static key_c4() { RunPythonStatement("ToggleTrace()"); }')
CompileLine('static key_c5() { RunPythonStatement("ToggleBP()"); }')

AddHotkey('Alt-1', 'key_1')
AddHotkey('Alt-2', 'key_2')
AddHotkey('Alt-3', 'key_3')
AddHotkey('Alt-4', 'key_4')
AddHotkey('Alt-5', 'key_5')
AddHotkey('Ctrl-4', 'key_c4')
AddHotkey('Ctrl-5', 'key_c5')

print 'ALT-1 : Start(Resume)/Pause Codemap'
print 'ALT-2 : Set Function Trace Point'
print 'ALT-3 : Set Range Trace Point'
print 'ALT-4 : Create/Setup Module Trace'
print 'ALT-5 : Connect Codemap Graph with IDA'
print 'Ctrl-4 : Toggle Trace Point'
print 'Ctrl-5 : Toggle BP only. Independent with Trace Point. Use this one than F2'
print 'Codemap Python Plugin is ready. enjoy. - by daehee'
