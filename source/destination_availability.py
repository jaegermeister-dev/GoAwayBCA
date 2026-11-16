"""Explain an occupied/missing destination before any driver preparation."""
from install_windows7 import require,Refusal

def check(targets,scan,interaction):
    require(scan.get('scan_complete') is True,'Installation scan incomplete; resolve the unreadable partition before continuing')
    occupied={r['target']['partition']['DeviceIdentifier'] for r in scan['installations']}
    eligible=[t for t in targets if t['partition']['DeviceIdentifier'] not in occupied]
    if eligible:return eligible
    names=[]
    for row in scan['installations']:
        p=row['target']['partition']
        names.append(str(row.get('title') or p.get('VolumeName') or 'Windows')+' ('+p['DeviceIdentifier']+')')
    message='No empty exFAT/NTFS destination is available.'
    if names:
        message+='\n\nExisting Windows installations:\n'+'\n'.join(names)
        message+='\n\nTo replace one, return to Manage Installations and delete it there first. To keep it, prepare a separate exFAT/NTFS partition yourself.'
    else:
        message+='\n\nNeither partition-detection method found an eligible destination. Check Disk Utility for your intended exFAT/NTFS partition. If you already have an empty eligible partition on an internal GPT drive, this may be a partition-detection problem. Please raise a GitHub issue and attach the saved log and partition-detection report.'
        message+='\n\nYou can also try Experimental troubleshooting: Reverse partition detection order. GoAwayBCA does not create or resize partitions.'
    message+='\n\nNothing was formatted. No drivers were downloaded or ISO preparations created by this attempt.'
    interaction.request('notice',message,button_title='Done')
    raise Refusal(message)
