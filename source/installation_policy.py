"""Model/image guidance and release policy; no hardware or filesystem changes.

An enabled recommendation is NOT proof that a suitable EFI payload has been
implemented. The publisher must additionally validate the selected profile.
"""

WIN7_LIMITATIONS = ('Built-in camera, sleep/resume repair and Thunderbolt dock Ethernet are deferred. Bluetooth discovery was tested; pairing was not. Fresh combined-install acceptance is pending.')

RETRY_GUIDANCE = (
    'To retry, you must erase and reinstall ONLY that Windows partition. '
    'Back up its files. For a verified GoAwayBCA or BackToSeven installation, '
    'use Manage Installations to delete it, format its partition as empty NTFS, '
    'and update the boot menu. Other installations must be removed manually: '
    'format only their Windows partition, then use Rebuild Boot Menu. '
    'Manual formatting can leave boot files behind. '
    'Installation requires an empty exFAT or NTFS destination. '
    'Unknown EFI files are preserved and reported for manual review. This erases all files on that '
    'Windows partition: back them up first. Do not erase the whole disk, '
    'macOS, OpenCore or another Windows installation.'
)
CLEANUP_WARNING = (
    'This installation has verified creation records, but some EFI files may '
    'be shared or owned by another installer. '
    'You may still proceed after the additional warnings and both '
    'formatting confirmations. All files on the selected partition will be '
    'erased. Unattributed or shared EFI files must not be blindly deleted; '
    'Files left untouched are listed in your saved log. '
    'Leftover boot files may remain and could cause unusual or unexpected '
    'boot behaviour, including stale menu entries or failed boot attempts. '
    'The review lists the files we will leave untouched because ownership '
    'or exclusive use could not be established. OpenCore and Apple EFI files '
    'are never removed by this operation. Save the operation report and follow '
    'the Manual EFI Cleanup guide if you choose to review leftovers yourself; '
    'that cleanup must be done manually. Only attempt manual cleanup if you '
    'know what you are doing. A mistake can make macOS, Windows or another '
    'installed OS unbootable. If unsure, leave the files and ask for help.'
)
UNOWNED_WARNING = (
    'Not verified as created by GoAwayBCA or its BackToSeven component. '
    'GoAwayBCA cannot uninstall this installation. A missing or invalid record '
    'does not prove another tool created it. You may still add it to the Windows '
    'boot menu or rename its menu entry. To remove it, back up its files first, '
    'then manually format only its Windows partition and use Rebuild Boot Menu. '
    'Do not erase the whole disk, the EFI partition or another operating system. '
    'Shared or unidentified EFI files are preserved; rebuilding the menu does '
    'not promise to remove every boot file left by another installer.'
)


def efi_policy(model, family):
    """Choose the UI policy from the SELECTED image, never the ISO filename."""
    if family not in ('Windows 7', 'Windows 8', 'Windows 8.1', 'Windows 10', 'Windows 11'):
        raise ValueError('This build targets Windows 7, 8, 8.1, 10 and 11.')
    if family in ('Windows 8.1', 'Windows 10', 'Windows 11'):
        return dict(default_enabled=False, mandatory=False, show_choice=False,
                    status='stock-efi', message=family+' uses the original Microsoft EFI loader. '
                    'The general experimental EFI patch is disabled.')
    if family == 'Windows 11':
        # Owner policy: inherit Windows 10 boot/driver handling on EVERY model.
        # The separate Windows 11 image/build/CPU checks remain in force.
        result=dict(efi_policy(model,'Windows 10'))
        result.update(show_choice=True, mandatory=False, status='optional-windows10-guidance',
                      policy_basis='Windows 10',
                      message=('Windows 11 follows the Windows 10 boot and driver workflow on '+model+'. '
                               'Its EFI patch choice remains optional. '+
                               ('Leave patches unticked; they are not recommended unless boot problems occur.'
                                if not result['default_enabled'] else
                                'Trying patches first is recommended from the Windows 10 guidance for this model, '
                                'but is not a confirmed Windows 11 requirement.')))
        return result
    if model == 'MacBookPro11,5' and family == 'Windows 8':
        workflow = 'Windows 8.1'
        return dict(default_enabled=False, mandatory=False, show_choice=False,
                    status='shared-stock-workflow', message=family+' uses the '+workflow+
                    ' installation workflow and stock boot path on this model, without the Windows 7 EFI fix.')
    if model == 'MacBookPro11,5' and family == 'Windows 10':
        return dict(default_enabled=False, mandatory=False, show_choice=False,
                    status='tested-stock', message='Windows 10 works on the tested 11,5 without the EFI fix. It is always disabled for this combination.')
    if model == 'MacBookPro11,5' and family == 'Windows 8.1':
        return dict(default_enabled=False, mandatory=False, show_choice=False,
                    status='tested-stock', message='Windows 8.1 works on the tested 11,5 with original Boot Camp drivers and without the Windows 7 EFI fix. The fix is always disabled for this combination.')
    if model == 'MacBookPro11,5' and family == 'Windows 7':
        return dict(default_enabled=True, mandatory=True, show_choice=False,
                    status='tested-with-limitations', message=(
                        'Windows 7 has been confirmed working on MacBookPro11,5 '
                        'with the validated EFI fixes. These are required and '
                        'cannot be disabled for this model. ' + WIN7_LIMITATIONS))
    if model == 'MacBookPro11,4' and family == 'Windows 7':
        return dict(default_enabled=False, mandatory=False, show_choice=False, status='tested-stock',
                    message='Windows 7 boots on MacBookPro11,4 with its original EFI path. The 11,5 graphics fix is not applied; it prevented boot on this model. Intel Iris and HDMI audio use the original signed Intel drivers.')
    if model == 'MacBookPro11,3' and family in ('Windows 7','Windows 8.1'):
        return dict(default_enabled=False, mandatory=False, show_choice=False, status='tested-stock',
                    message=family+' and its official Boot Camp drivers worked on our MacBookPro11,3 '
                    'without experimental EFI patches. Patches are disabled for this combination.')
    if model == 'MacBookPro11,3' and family == 'Windows 8':
        return dict(default_enabled=False, mandatory=False, show_choice=True, status='adjacent-version-evidence',
                    message='EFI patches are not recommended for the first attempt. We strongly recommend leaving '
                    'them unticked: Windows 7 and Windows 8.1 worked without them on our MacBookPro11,3. '
                    'This recommendation is inferred for Windows 8; consider patches only if boot problems occur.')
    if ((model == 'MacBookPro11,3' and family in ('Windows 7','Windows 8.1','Windows 10')) or
        (model == 'MacBookPro11,4' and family in ('Windows 8.1','Windows 10'))):
        # Owner-selected baseline, pending the exact pair's fresh hardware test.
        # A fixed UI policy must not be described as evidence of a passed test.
        return dict(default_enabled=False, mandatory=False, show_choice=False,
                    status='fixed-stock-baseline', message=(
                        family+' uses the unpatched EFI boot path on '+model+'. '
                        'Experimental EFI patches are disabled for this combination. '
                        'Fresh hardware acceptance of this build is still pending.'))
    # Windows 8 on the 11,3 remains explicitly selectable and OFF by default.
    native_pair=(model=='MacBookPro11,3' and family=='Windows 8')
    if not native_pair:
        # Other database pairs may gain verified Apple mode evidence later.
        # Unknown, Winclone-only, legacy and architecture evidence are not EFI
        # installation proof. Match the exact model and Windows family.
        from mac_compatibility import load
        row=load()['models'].get(model,{}).get('boot_camp_installation_mode',{}).get(family,{})
        native_pair=(row.get('status') in ('pure-efi','uefi','efi') and bool(row.get('sources')))
    if native_pair:
        return dict(default_enabled=False,mandatory=False,show_choice=True,
                    status='native-efi-baseline',
                    message=(family+' has a native EFI boot path on '+model+'. Experimental EFI patches are '
                             'not recommended unless you experience issues. Leave them unticked for your '
                             'first attempt. This exact combination still awaits our hardware acceptance test.'))
    if model == 'MacBookPro11,3':
        message = (family+' on MacBookPro11,3 has not yet been tested with this build. '
                   'Choose with or without the experimental EFI fix for comparison. '
                   'Start without patches for the baseline test. No 11,3-specific compatibility modifications have been made. '+RETRY_GUIDANCE)
    else:
        legacy=row.get('status') in ('hybrid-mbr','legacy-bios','legacy','bios') and bool(row.get('sources'))
        reason=('Boot Camp used legacy BIOS/hybrid MBR for '+family+' on '+model+'. '
                if legacy else 'The historical Boot Camp boot mode for this exact model/Windows pair is unconfirmed. ')
        message = (reason+'Experimental EFI fixes are ticked by default; '+
                   ('we recommend trying them first. ' if legacy else 'this is an experimental starting point, not a confirmed requirement. ')+
                   'You may untick them. They may not work. If you encounter issues, raise a GitHub issue '
                   'with the diagnostic and hardware reports so we can investigate this Mac/Windows combination. '+RETRY_GUIDANCE)
    return dict(default_enabled=model!='MacBookPro11,3', mandatory=False, show_choice=True,
                status='experimental-untested', message=message)


def validate_efi_choice(model, family, enabled):
    policy = efi_policy(model, family)
    if type(enabled) is not bool:
        raise ValueError('EFI choice must be explicit.')
    if policy['mandatory'] and not enabled:
        raise ValueError('The validated Windows 7 EFI fixes are mandatory on ' + model)
    if not policy['show_choice'] and enabled != policy['default_enabled']:
        raise ValueError('This model/Windows combination has a fixed EFI policy.')
    return policy
