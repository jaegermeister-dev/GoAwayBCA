"""Pre-format driver choice and verified additive copy; no driver installation."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import concurrent.futures
import threading
import bootcamp_download as apple
import community_download
import publisher_helpers
import driver_sources
from optional_verification import control, stop_owned_reader
from operation_progress import Meter, phase, hash_file, run_phase
from install_windows7 import Refusal

WORKING_115='Confirmed individually on both models: graphics acceleration, Retina resolution, built-in and dock HDMI video/audio, Wi-Fi, analog audio and internal microphone, SD reader, keyboard input, Apple keyboard layouts, Fn controls, keyboard backlight and Boot Camp trackpad functions including scrolling. Battery/AC transitions passed. Bluetooth device discovery passed; pairing was not tested. Built-in cameras, sleep/resume repair and dock Ethernet remain deferred. Sleep can leave input, SD or dock devices unavailable until a restart. Connect the dock before booting.'

def configure(interaction,detected_model,family,model_guidance=None):
    from older_windows_policy import driver_plan
    fallback=driver_plan(model_guidance,detected_model,family)
    if fallback is not None:return fallback
    def official_choice():
        return ('skip' if interaction.choose('Optional Windows drivers. Prepare original Apple drivers now from macOS so they are available '
            'even if fresh Windows has no network connection. Nothing will be installed automatically.',
            ['Prepare official Apple drivers (recommended)','Skip official Apple download'])==1 else 'apple')
    # On these unsupported Win7 models, first confirm the model and then offer
    # community support ONLY. Do not misleadingly offer a matching Apple pack.
    offer_community=family=='Windows 7' and detected_model in ('MacBookPro11,5','MacBookPro11,4')
    source=None if offer_community else official_choice()
    if source=='skip':
        return dict(source='skip',detected_model=detected_model)
    while True:
        entered=interaction.request('driver_model','Confirm the Mac model for DRIVERS, or replace it with '
            'the intended model if this Mac is spoofed or you are preparing another Mac. '
            'This does not change hardware detection or EFI-fix policy.',model=detected_model)
        try:model=apple.model_identifier(entered);break
        except ValueError as error:
            if interaction.choose(str(error),['Enter the model again','Skip drivers'])==1:
                return dict(source='skip',detected_model=detected_model)
    if family=='Windows 7' and model in ('MacBookPro11,5','MacBookPro11,4'):
        message=('Apple did not officially support Windows 7 on '+model+
            ' and does not provide an official Windows 7 driver package for this model.\n\n'+WORKING_115)
        if model=='MacBookPro11,4':message+=('\n\nThe 11,4 receives the accepted Intel Iris and HDMI-audio drivers, '
            'with AMD M370X and AMD HDMI-audio drivers excluded.')
        message+=('\n\nUse our community Windows 7 driver support instead, or skip drivers? '
            'BackToSeven uses our verified, versioned support kit, '
            'then retrieves the matching original drivers directly from their publishers. '
            'It copies the assembled pack to the Windows partition root; you run its installer after Windows setup.')
        selected=interaction.choose(message,['Use our community Windows 7 support','Skip drivers'])
        source='community' if selected==0 else 'skip'
    elif source is None:
        # The owner corrected a spoofed 11,4/11,5 identity to a different model.
        source=official_choice()
    return dict(source=source,model=model,detected_model=detected_model,family=family)

def prepare(plan,resources,session,tool,interaction,note):
    if plan['source']=='skip':return None
    while True:
        try:
            results=[]
            if plan['source'] in ('community','apple+community'):
                def get_community():
                    return community_download.prepare(plan['model'],plan['family'],Path(resources),session,
                        publisher_helpers.select(resources),note)
                result=driver_sources.source(plan,session,interaction,note,get_community,'community')
                if result is not None:
                    result=community_download.refresh_installer(result,plan['model'],plan['family'],resources,session,note,
                        extraction_tools=publisher_helpers.select(resources))
                    results.append(result)
            if plan['source'] in ('apple','apple+community'):
                result=driver_sources.source(plan,session,interaction,note,
                    lambda:apple.prepare_apple(plan['model'],plan.get('driver_family',plan['family']),session,interaction,note,
                        **({'selected_family':plan['family']} if plan.get('older_windows_fallback') else {})),'official')
                if result is not None:results.append(result)
            if not results:return None
            if len(results)==1:
                root=results[0]['root'];receipt=results[0]['receipt']
            else:
                import shutil
                root=Path(tempfile.mkdtemp(prefix='combined-drivers-',dir=str(session)))/'overlay';root.mkdir()
                for result in results:
                    for folder in result['root'].iterdir():
                        apple.require(not (root/folder.name).exists(),'Driver preparations overlap')
                        shutil.move(str(folder),str(root/folder.name))
                receipt=dict(source='official-apple-and-separate-community',packages=[r['receipt'] for r in results])
            paths=sorted(p for p in root.rglob('*') if p.is_file())
            meter=Meter(note,'Verifying staged driver files',len(paths),'files verified')
            files={};sizes={}
            for number,path in enumerate(paths,1):
                relative=path.relative_to(root).as_posix()
                files[relative]=apple.sha256(path);sizes[relative]=path.stat().st_size
                meter.update(number,relative)
            size=sum(sizes.values())
            wim=root.parent/(root.name+'-drivers.wim')
            run_phase([tool,'capture',root,wim,'Optional Windows drivers','--compress=none','--check'],
                      note,'Preparing driver transfer image')
            return dict(wim=wim,wim_sha256=hash_file(wim,note,'Hashing driver transfer image'),
                        files=files,sizes=sizes,bytes=size,receipt=receipt)
        except (ValueError,OSError,subprocess.SubprocessError,Refusal) as error:
            # Cancellation from the GUI must stay cancellation, never another prompt.
            if str(error).startswith('Cancelled by user'):raise
            note('Driver preparation failed before formatting: '+str(error))
            action=interaction.choose('Drivers could not be prepared. No target has been formatted.\n\n'+str(error),
                ['Retry driver preparation','Continue without drivers'])
            if action==1:return None

def verify_copies(prepared,ntfscat,target,guard,note,clock=time.monotonic,interaction=None):
    """Read-only batches: full hashes, fresh guards before/after and every 64
    files or 10 seconds. All WRITE-boundary guards remain unconditional.
    No --force, mounts, signature changes or driver execution.
    """
    guard();checked=0;last_check=clock()
    meter=Meter(note,'Verifying driver files',len(prepared['files']),'files verified')
    current={};lock=threading.Lock();stop=threading.Event();readers={};aborted=set()
    class ReaderStopped(Exception):pass
    class ReadbackFailure(ValueError):
        def __init__(self,item,code,read,digest,error):
            self.item=item
            rel,expected=item
            size=prepared.get('sizes',{}).get(rel)
            super().__init__('Driver copy readback failed: %s\nExit code: %s; expected bytes: %s; read bytes: %d\n'
                'Expected SHA256: %s\nRead SHA256: %s\nReader diagnostic: %s'%
                (rel,code,size if size is not None else 'not recorded',read,expected,digest,error or '(none)'))
    def stop_readers():
        stop.set()
        # Register and terminate under the same lock: a newly starting reader
        # cannot escape the stop request. Only these owned ntfscat readers are
        # touched; image-copy/format helpers are not in this registry.
        with lock:
            for process in list(readers.values()):
                if process.poll() is None:
                    aborted.add(process)
                    stop_owned_reader(process)
    def read_one(item):
        rel,expected=item
        if stop.is_set():raise ReaderStopped()
        from ntfs_read import components
        components('/'+rel)
        h=hashlib.sha256();read=0;code='not started';error='';process=None;read_error=False
        size=prepared.get('sizes',{}).get(rel)
        # stderr goes to a bounded-on-read temporary file, not a pipe that can
        # deadlock while stdout is drained. Reap even on a hash/read exception.
        with tempfile.TemporaryFile() as errors:
            try:
                with subprocess.Popen([str(ntfscat),str(target),'/'+rel],stdout=subprocess.PIPE,stderr=errors) as process:
                    try:
                        with lock:
                            readers[rel]=process
                            if stop.is_set() and process.poll() is None:
                                aborted.add(process);stop_owned_reader(process)
                        for chunk in iter(lambda:process.stdout.read(1024**2),b''):
                            read+=len(chunk);h.update(chunk)
                            if size is not None and read>size:
                                error='Read stopped after exceeding expected length.'
                                stop_owned_reader(process);break
                            # Only the coordinator writes IPC/logs.
                            with lock:current[rel]=read
                        code=process.wait()
                    except BaseException:
                        stop_owned_reader(process)
                        raise
                    finally:
                        with lock:readers.pop(rel,None)
            except OSError as failure:
                read_error=True
                code=process.poll() if process is not None else 'not started'
                error=str(failure)
            errors.seek(0);error=(error+' '+errors.read(1500).decode('utf-8','replace')).strip()
        if process in aborted and code!=0:raise ReaderStopped()
        if read_error or code!=0 or h.hexdigest()!=expected or (size is not None and read!=size):
            raise ReadbackFailure(item,code,read,h.hexdigest(),error)
        return rel
    # Four bounded, read-only helpers amortise process launch latency. At most
    # 64 files can be read between batch identity checks. No write runs here.
    # Hashes still cover every byte of every file, not sampling or size checks.
    items=list(prepared['files'].items())
    requested=False
    with control(interaction,'Verifying driver files') as skipped, concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0,len(items),64):
            if start:guard();last_check=clock()
            with lock:current.clear()
            pending={pool.submit(read_one,item) for item in items[start:start+64]}
            failed=[]
            try:
                while pending:
                    done,pending=concurrent.futures.wait(pending,timeout=.25,
                        return_when=concurrent.futures.FIRST_COMPLETED)
                    if clock()-last_check>=10:guard();last_check=clock()
                    detail=''
                    for future in done:
                        try:detail=future.result();checked+=1
                        except ReadbackFailure as failure:failed.append(failure)
                    # A known failed read must never be hidden by skipping.
                    if pending and skipped():
                        if failed:raise failed[0]
                        requested=True;stop_readers()
                        for future in pending:
                            if future.cancel():continue
                            try:future.result();checked+=1
                            except ReaderStopped:pass
                        break
                    if not detail:
                        with lock:
                            if current:
                                rel,read=list(current.items())[-1]
                                detail='%s: %d bytes read'%(rel,read)
                    # Final completion waits for the final identity guard.
                    if checked<len(items):meter.update(checked,detail)
                # Drain the batch before retrying: only one reader runs during
                # each retry, and only failed files are reread. Two retries max.
                for failure in failed:
                    note(str(failure))
                    for attempt in (2,3):
                        if skipped():raise failure
                        guard();last_check=clock()
                        rel=failure.item[0]
                        note('Retrying driver verification separately: %s (attempt %d of 3)'%(rel,attempt))
                        with lock:current.clear()
                        retry=pool.submit(read_one,failure.item);pending={retry}
                        while not retry.done():
                            concurrent.futures.wait(pending,timeout=.25)
                            if clock()-last_check>=10:guard();last_check=clock()
                            if skipped() and not retry.done():raise failure
                        pending=set()
                        try:
                            retry.result();checked+=1
                            note('Driver file verified on retry: '+rel)
                            if checked<len(items):meter.update(checked,rel)
                            break
                        except ReadbackFailure as retry_failure:
                            failure=retry_failure
                            if attempt==3:
                                raise ValueError('Driver verification stopped after 3 read attempts. '+str(failure))
                            note(str(failure))
            except BaseException:
                stop_readers()
                for future in pending:future.cancel()
                # Running readers are joined/reaped before control returns.
                # They perform no writes and cannot publish a menu or success.
                raise
            if requested:break
    guard()
    if requested:
        note('Driver verification skipped by user: %d of %d files verified. Remaining files were not fully checked.'%
            (checked,len(prepared['files'])))
        phase(note,'Driver verification skipped by user')
    else:
        meter.update(checked,force=True)
        note('Driver verification complete: %d of %d files verified.'%(checked,len(prepared['files'])))
    return dict(status='skipped-by-user' if requested else 'verified',files_verified=checked,total_files=len(items))


def apply(prepared,tool,ntfscat,target,guard,note,interaction=None):
    if prepared is None:return
    apple.require(hash_file(prepared['wim'],note,'Verifying driver transfer image')==prepared['wim_sha256'],
                  'Prepared driver image changed')
    guard();note('Copying optional drivers to the Windows partition root. They will not run automatically.')
    run_phase([tool,'apply',prepared['wim'],'1',target,'--check'],note,'Copying drivers')
    note('Driver copy complete. Verifying copied driver files...')
    return verify_copies(prepared,ntfscat,target,guard,note,interaction=interaction)
