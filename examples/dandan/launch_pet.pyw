"""Windowless entry point. Keep app output inspectable without a terminal."""
import contextlib
import ctypes
import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import runpy
import sys
from app_paths import FROZEN,user_data_root

ROOT=Path(__file__).resolve().parent
LOG_PATH=user_data_root()/'logs/launcher.log'


def acquire_instance_lock():
    if sys.platform!='win32':return None,False
    import hashlib
    token=hashlib.sha256(str(user_data_root().resolve()).lower().encode('utf-8')).hexdigest()[:20]
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateMutexW.argtypes=(ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p)
    api.CreateMutexW.restype=ctypes.c_void_p
    ctypes.set_last_error(0)
    handle=api.CreateMutexW(None,False,'Local\\DandanDesktopPet-'+token)
    if not handle:raise ctypes.WinError(ctypes.get_last_error())
    return handle,ctypes.get_last_error()==183


def release_instance_lock(handle):
    if handle:
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CloseHandle.argtypes=(ctypes.c_void_p,)
        api.CloseHandle(handle)


class LogStream(io.TextIOBase):
    def __init__(self,logger):
        self.logger=logger
        self.pending=''

    @property
    def encoding(self):return 'utf-8'

    def writable(self):return True

    def write(self,text):
        self.pending+=text
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1)
            if line:self.logger.info(line.rstrip('\r'))
        return len(text)

    def flush(self):
        if self.pending:
            self.logger.info(self.pending);self.pending=''
        for handler in self.logger.handlers:handler.flush()


def show_error(message):
    if os.environ.get('DOGPET_DIAGNOSTIC_NO_DIALOG')=='1':return
    if sys.platform=='win32':
        ctypes.windll.user32.MessageBoxW(None,message,'诞诞桌宠',0x10)


def main(argv=None):
    logger=logging.getLogger('dogpet.windowless')
    logger.setLevel(logging.INFO);logger.propagate=False
    handler=None;stream=None;instance_handle=None
    try:
        LOG_PATH.parent.mkdir(parents=True,exist_ok=True)
        handler=RotatingFileHandler(LOG_PATH,maxBytes=2*1024*1024,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
        stream=LogStream(logger)
        os.chdir(ROOT)
        if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
        arguments=list(sys.argv[1:] if argv is None else argv)
        allow_multiple='--allow-multiple' in arguments
        arguments=[a for a in arguments if a!='--allow-multiple']
        sys.argv=[str(ROOT/'pet_alpha.py'),*arguments]
        if FROZEN and not allow_multiple:
            instance_handle,already_running=acquire_instance_lock()
            if already_running:
                logger.info('already_running');return 0
        console=int(ctypes.windll.kernel32.GetConsoleWindow() or 0) if sys.platform=='win32' else None
        logger.info('windowless_start pid=%s console_hwnd=%s frozen=%s resource_root=%s args=%s',os.getpid(),console,FROZEN,ROOT,sys.argv[1:])
        with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
            try:
                if FROZEN:
                    import pet_alpha
                    raise SystemExit(pet_alpha.main())
                else:
                    runpy.run_path(str(ROOT/'pet_alpha.py'),run_name='__main__')
            except SystemExit as exc:
                code=0 if exc.code is None else (exc.code if isinstance(exc.code,int) else 1)
                if code:
                    logger.error('application_exit code=%s detail=%s',code,exc.code)
                    show_error(f'小狗没能正常启动或运行。\n详细原因已保存到：\n{LOG_PATH}')
                return code
        return 0
    except Exception:
        if handler:logger.exception('windowless_launch_failed')
        show_error(f'小狗启动失败。\n请先完整解压程序文件夹，再打开小狗。\n日志位置：\n{LOG_PATH}')
        return 1
    finally:
        release_instance_lock(instance_handle)
        if stream:stream.flush()
        if handler:
            logger.removeHandler(handler);handler.close()


if __name__=='__main__':raise SystemExit(main())
