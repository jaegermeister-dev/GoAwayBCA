/* Native Cocoa front end. No disk-writing implementation lives in the GUI.

 * Avoid Swift, ARC, blocks, storyboards and JSON to permit older SDK builds.

 * Intended x86_64/10.7+ build; actual old-host execution remains unvalidated.

 */

#import <Cocoa/Cocoa.h>

#import <CommonCrypto/CommonDigest.h>

#include <sys/socket.h>

#include <sys/un.h>

#include <sys/stat.h>

#include <sys/utsname.h>

#include <sys/sysctl.h>

#include <arpa/inet.h>

#include <unistd.h>

#include <signal.h>

#include <fcntl.h>

#include <errno.h>

#include <math.h>

#include "authorization_channel.h"

#include "firmware_dialog.h"



static NSString *Resources;

static BOOL DemoMode = NO;

static BOOL SelfTest = NO;

static NSSize TestDialogSpace = {0,0};

static NSString *BackupWarningPreference = @"SuppressCompleteDiskBackupWarning";

static NSString *SecurityChipWarningPreference = @"SuppressUntestedT1T2Warning-v2";

static NSString *WindowFramePreference = @"GoAwayBCA Main Window v1";



static NSRect FitWindowFrame(NSRect proposed, NSRect visible) {

    proposed.size.width = MIN(MAX(proposed.size.width,640),visible.size.width);

    proposed.size.height = MIN(MAX(proposed.size.height,500),visible.size.height);

    proposed.origin.x = MAX(NSMinX(visible),MIN(proposed.origin.x,NSMaxX(visible)-proposed.size.width));

    proposed.origin.y = MAX(NSMinY(visible),MIN(proposed.origin.y,NSMaxY(visible)-proposed.size.height));

    return proposed;

}



static NSString *SupportURL(NSInteger response) {

    /* Reserved destinations only. Opening a link does not create a project or

     * community, authenticate an account, or upload logs. */

    if (response == NSAlertFirstButtonReturn) return @"https://github.com/jaegermeister-dev/GoAwayBCA";

    if (response == NSAlertSecondButtonReturn) return @"https://www.reddit.com/r/GoAwayBCA/";

    return nil;

}

static NSAlert *SupportAlert(void) {

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    [alert setMessageText:@"GoAwayBCA support"];

    [alert setInformativeText:@"GoAwayBCA Beta 1. For help or to report a problem, open a GitHub issue and include the saved log and diagnostic report. Review reports for personal information before sharing them.\n\nReddit opens r/GoAwayBCA. The GitHub link opens the GoAwayBCA repository. No logs or hardware reports are uploaded automatically."];

    [alert addButtonWithTitle:@"GitHub"];

    [alert addButtonWithTitle:@"Reddit"];

    [alert addButtonWithTitle:@"Close"];

    return alert;

}

static NSAlert *DriverChoiceAlert(NSString *kind, NSString *text) {

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    BOOL mismatch = [kind isEqual:@"driver_mismatch"];

    [alert setMessageText:mismatch ? @"Saved drivers have changed" : @"Save downloaded drivers"];

    [alert setInformativeText:text];

    NSArray *buttons = mismatch ? [NSArray arrayWithObjects:@"Use anyway",@"Move to Desktop & redownload",@"Delete & redownload",@"Cancel and go back",nil]

        : [NSArray arrayWithObjects:@"Default location",@"Custom location…",@"Open Drivers Folder",@"Cancel",nil];

    for (NSString *title in buttons) [alert addButtonWithTitle:title];

    [alert setAccessoryView:[[[NSView alloc] initWithFrame:NSMakeRect(0,0,540,1)] autorelease]];

    if (mismatch) {

        [[[alert buttons] objectAtIndex:0] setKeyEquivalent:@""];

        [[[alert buttons] objectAtIndex:3] setKeyEquivalent:@"\r"];

    }

    return alert;

}



/* Keep lengthy partition reviews inside a scrolling region. Inputs and action

 * buttons stay outside it, including on 800x600 displays. No approval is sent

 * when the separate Details window is opened or closed. */

static void BoundAlertText(NSAlert *alert, NSSize available, BOOL force) {

    NSString *text = [alert informativeText];

    CGFloat textHeight=[text boundingRectWithSize:NSMakeSize(500,100000)

        options:NSStringDrawingUsesLineFragmentOrigin

        attributes:@{NSFontAttributeName:[NSFont systemFontOfSize:13]}].size.height;

    if (!force && textHeight+[[alert accessoryView] frame].size.height+250<=available.height

        && available.width>=800) return;

    NSView *controls = [[alert accessoryView] retain];

    [alert setAccessoryView:nil];

    [controls removeFromSuperview];

    CGFloat width = MIN(620,MAX(300,available.width-180));

    CGFloat controlsHeight = controls ? [controls frame].size.height : 0;

    CGFloat extraButtons=MAX(0,(NSInteger)[[alert buttons] count]-2)*36;

    CGFloat height = MIN(260,MAX(60,available.height-250-controlsHeight-extraButtons));

    NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,width,height+controlsHeight+12)] autorelease];

    NSScrollView *scroll = [[[NSScrollView alloc] initWithFrame:NSMakeRect(0,controlsHeight+12,width,height)] autorelease];

    [scroll setHasVerticalScroller:YES]; [scroll setBorderType:NSBezelBorder];

    NSTextView *content = [[[NSTextView alloc] initWithFrame:NSMakeRect(0,0,width-20,height)] autorelease];

    [content setEditable:NO]; [content setSelectable:YES]; [content setRichText:NO];

    [content setFont:[NSFont systemFontOfSize:13]];

    [content setHorizontallyResizable:NO]; [content setVerticallyResizable:YES];

    [content setAutoresizingMask:NSViewWidthSizable];

    [[content textContainer] setWidthTracksTextView:YES];

    [content setString:text ?: @""]; [scroll setDocumentView:content];

    [box addSubview:scroll];

    if (controls) {

        for (NSView *child in [controls subviews]) [child setAutoresizingMask:NSViewWidthSizable|NSViewMinYMargin];

        [controls setFrame:NSMakeRect(0,0,width,controlsHeight)]; [box addSubview:controls];

    }

    [alert setInformativeText:@""]; [alert setAccessoryView:box];

    [controls release]; [alert layout];

    /* AppKit chrome differs between macOS versions. Fit the measured window,

     * not just an estimated title/button height; keep the controls outside

     * the scroll area and never clip confirmation buttons. */

    for (unsigned pass=0;pass<3;pass++) {

        CGFloat excess=ceil([[alert window] frame].size.height-available.height);

        if (excess<=0 || height<=60) break;

        height=MAX(60,height-excess-2);

        [scroll setFrameSize:NSMakeSize(width,height)];

        [box setFrameSize:NSMakeSize(width,height+controlsHeight+12)];

        [alert layout];

    }

}



static NSSize DialogSpace(void) {

    if (SelfTest && TestDialogSpace.width) return TestDialogSpace;

    NSScreen *screen=[NSApp keyWindow] ? [[NSApp keyWindow] screen] : [NSScreen mainScreen];

    return screen ? [screen visibleFrame].size : NSMakeSize(800,600);

}



static NSAlert *DetailsAlert(NSString *text) {

    NSAlert *alert=[[[NSAlert alloc] init] autorelease];

    [alert setMessageText:@"EFI cleanup details"];

    [alert setInformativeText:text]; [alert addButtonWithTitle:@"Back to review"];

    BoundAlertText(alert,DialogSpace(),YES);

    return alert;

}



static NSTextView *EditableText(NSView *view) {

    if ([view isKindOfClass:[NSTextView class]] && [(NSTextView *)view isEditable]) return (NSTextView *)view;

    for (NSView *child in [view subviews]) {

        NSTextView *found=EditableText(child);

        if (found) return found;

    }

    return nil;

}



static NSString *HardwareModel(void) {

    char value[256]; size_t size = sizeof(value);

    memset(value,0,sizeof(value));

    if (sysctlbyname("hw.model",value,&size,NULL,0) || !size || size > sizeof(value)

        || value[size-1] != '\0') return @"Unknown model";

    NSString *model = [NSString stringWithUTF8String:value];

    return [model length] ? model : @"Unknown model";

}

static NSString *SecurityChipForModel(NSString *model) {

    /* Exact identifiers: the non-Touch Bar 13,1 and 14,1 do not have T1.

     * This is reported-model detection, not proof against SMBIOS spoofing.

     * Provenance: SECURITY-CHIP-MODELS.txt. */

    NSArray *t1 = [NSArray arrayWithObjects:@"MacBookPro13,2",@"MacBookPro13,3",

        @"MacBookPro14,2",@"MacBookPro14,3",nil];

    NSArray *t2 = [NSArray arrayWithObjects:

        @"MacBookPro15,1",@"MacBookPro15,2",@"MacBookPro15,3",@"MacBookPro15,4",

        @"MacBookPro16,1",@"MacBookPro16,2",@"MacBookPro16,3",@"MacBookPro16,4",

        @"MacBookAir8,1",@"MacBookAir8,2",@"MacBookAir9,1",@"Macmini8,1",

        @"iMacPro1,1",@"iMac20,1",@"iMac20,2",@"MacPro7,1",nil];

    if ([t1 containsObject:model]) return @"T1";

    if ([t2 containsObject:model]) return @"T2";

    return nil;

}

static NSString *SecurityChipGuidance(NSString *model) {

    NSString *chip = SecurityChipForModel(model);

    if (!chip) return nil;

    if ([chip isEqual:@"T1"])

        return @"This Mac has an Apple T1 chip. T1 Macs have not been tested with GoAwayBCA and are not officially supported. You may try installing Windows, but it may not work. If you encounter problems, you may submit a GitHub issue with your hardware diagnostics. Investigation and a fix are not guaranteed.";

    return @"This Mac has an Apple T2 Security Chip. T2 Macs have not been tested with GoAwayBCA and are not officially supported. Installation or booting may fail even with the lowest startup security settings. T2 support will not be added, and T2-specific problems will not be addressed. Please do not submit GitHub issues or diagnostics for T2 compatibility. You may continue at your own risk. Apple's Boot Camp Assistant is the recommended route for installing Windows 10 on this Mac.";

}

static BOOL ShouldShowSecurityChipWarning(NSString *model, BOOL suppressed) {

    return !suppressed && SecurityChipForModel(model) != nil;

}

static NSAlert *SecurityChipWarning(NSString *model) {

    NSString *guidance = SecurityChipGuidance(model);

    if (!guidance) return nil;

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    [alert setMessageText:[NSString stringWithFormat:@"Untested Apple %@ Mac",SecurityChipForModel(model)]];

    [alert setInformativeText:guidance];

    [alert addButtonWithTitle:@"Continue"];

    [alert addButtonWithTitle:@"Quit"];

    [alert setShowsSuppressionButton:YES];

    [[alert suppressionButton] setTitle:@"Don't show me this message again"];

    [[alert suppressionButton] setState:NSOffState];

    [alert setAccessoryView:[[[NSView alloc] initWithFrame:NSMakeRect(0,0,540,1)] autorelease]];

    return alert;

}

static BOOL RememberSecurityChipWarningChoice(NSInteger response, NSInteger checkboxState) {

    return response == NSAlertFirstButtonReturn && checkboxState == NSOnState;

}

static NSString *HardwareModelGuidance(NSString *model) {

    if ([model isEqual:@"MacBookPro11,5"])

        return @"Windows 8.1 and Windows 10 have been tested on MacBookPro11,5. Windows 11 is available to try, with guidance based on Windows 10; it has not been tested on this model. Back up your data before using this beta.";

    if ([model isEqual:@"MacBookPro11,4"])

        return @"Windows 8.1 and Windows 10 have been tested on MacBookPro11,4, including installations on a drive with 4096-byte logical sectors. Windows 11 is available to try, with guidance based on Windows 10; it has not been tested on this model. Back up your data before using this beta.";

    /* The launch dialog is the sole T1/T2 warning. Do not repeat its advisory

     * in the information panel, especially after the user has suppressed it. */

    NSString *chip = SecurityChipForModel(model);

    if (chip) return [NSString stringWithFormat:@"Apple %@ model detected.",chip];

    return @"You may try this Intel Mac. After you confirm its model and select a Windows edition, GoAwayBCA checks the available Apple compatibility evidence, actual firmware architecture and CPU features. Hardware behaviour has not been confirmed on this model. Pure EFI only; hybrid MBR is never supported.";

}



static NSString *ModelGuidance(NSString *model) {
    return [HardwareModelGuidance(model) stringByAppendingString:@"\n\nLinux coexistence has not been tested, whether Linux was installed before, between or after Windows installations. GRUB may ignore our Windows menu, offer the whole menu, or detect individual Windows entries; any of these routes may or may not boot. Installing Linux or changing its bootloader later can also change boot files or firmware entries. OpenCore and the Mac's built-in Option/Alt picker may behave differently: either, both or neither OS may appear or boot. Try both pickers where available. If an entry is missing, OpenShell can be used to try launching the appropriate existing EFI loader directly; this is a possible workaround, not a guaranteed repair. Our menu lists Windows only and leaves other OS entries alone. Back up before changing a multi-OS boot configuration. Please report successes and failures with your Linux distribution/version, bootloader/version if known, installation method and order, disk layout, Mac model, Windows versions, GoAwayBCA version, OpenCore version if used, entries visible in each picker, and what actually booted. Include a GoAwayBCA diagnostic report. Linux testing is deferred beyond Beta 1."];
}

static NSAlert *BackupWarning(void) {

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    [alert setMessageText:@"Back up your disk before proceeding"];

    [alert setInformativeText:@"It is advised to have a complete backup of your disk before proceeding.\n\nIf Windows is already installed, back it up before changing partitions and confirm the disk has no hybrid MBR. Where possible, free space and shrink its partition from Windows Disk Management before creating another partition. This is a precaution for existing Boot Camp installs, including pure GPT/EFI; we have not established that Disk Utility always breaks a pure EFI installation. This app does not create, resize or repair partitions.\n\nInstalling Windows independently after using this app can change boot files. Menu recovery may not work in every configuration.\n\nHiding this reminder does not skip either formatting confirmation."];

    [alert addButtonWithTitle:@"Continue"];

    [alert addButtonWithTitle:@"Quit"];

    [alert setShowsSuppressionButton:YES];

    [[alert suppressionButton] setTitle:@"Do not show this message again"];

    [[alert suppressionButton] setState:NSOffState];

    return alert;

}

static BOOL RememberBackupWarningChoice(NSInteger response, NSInteger checkboxState) {

    return response == NSAlertFirstButtonReturn && checkboxState == NSOnState;

}

static void SaveViewPreview(NSView *view, NSString *path) {

    [view display];

    NSBitmapImageRep *bitmap = [view bitmapImageRepForCachingDisplayInRect:[view bounds]];

    [view cacheDisplayInRect:[view bounds] toBitmapImageRep:bitmap];

    /* NSAlert material is composited by WindowServer, outside this view.

     * Give exported offscreen QA a solid background so transparent black

     * text remains readable. This changes only the saved preview, not UI. */

    NSBitmapImageRep *opaque = [[[NSBitmapImageRep alloc] initWithBitmapDataPlanes:NULL

        pixelsWide:[bitmap pixelsWide] pixelsHigh:[bitmap pixelsHigh] bitsPerSample:8

        samplesPerPixel:4 hasAlpha:YES isPlanar:NO colorSpaceName:NSDeviceRGBColorSpace

        bytesPerRow:0 bitsPerPixel:0] autorelease];

    NSRect rect=NSMakeRect(0,0,[bitmap pixelsWide],[bitmap pixelsHigh]);

    [NSGraphicsContext saveGraphicsState];

    [NSGraphicsContext setCurrentContext:[NSGraphicsContext graphicsContextWithBitmapImageRep:opaque]];

    [[NSColor colorWithCalibratedWhite:0.95 alpha:1.0] setFill];

    NSRectFillUsingOperation(rect,NSCompositeCopy);

    NSImage *snapshot=[[[NSImage alloc] initWithSize:rect.size] autorelease];

    [snapshot addRepresentation:bitmap];

    [snapshot drawInRect:rect fromRect:NSZeroRect operation:NSCompositeSourceOver fraction:1.0];

    [[NSGraphicsContext currentContext] flushGraphics];

    [NSGraphicsContext restoreGraphicsState];

    if (![[opaque representationUsingType:NSPNGFileType properties:[NSDictionary dictionary]] writeToFile:path atomically:YES])

        [NSException raise:@"Preview" format:@"Could not save UI preview."];

}



static NSString *LayoutPreference = @"GoAwayBCALayoutDeclarationV1";

static NSAlert *SIPChoiceAlert(NSString *text) {
    NSAlert *alert=[[[NSAlert alloc] init] autorelease];
    [alert setMessageText:@"SIP and the disk-layout check"];
    [alert setInformativeText:text];
    [alert addButtonWithTitle:@"Disable SIP instructions"];
    [alert addButtonWithTitle:@"Keep SIP enabled"];
    [alert addButtonWithTitle:@"Continue as is / SIP is disabled"];
    BoundAlertText(alert,DialogSpace(),NO);
    return alert;
}

static NSString *SIPChoiceAction(NSInteger response) {
    if (response==NSAlertThirdButtonReturn) return @"automatic";
    if (response==NSAlertSecondButtonReturn) return @"manual";
    if (response==NSAlertFirstButtonReturn) return @"guide";
    return @"cancel";
}

static NSAlert *LayoutAlert(NSString *stage, NSString *text) {
    NSAlert *a=[[[NSAlert alloc] init] autorelease];
    [a setMessageText:@"Check your disk layout before continuing"];
    [a setInformativeText:text];
    if ([stage isEqual:@"question"] || [stage isEqual:@"existing"] || [stage isEqual:@"bootcamp"]) {
        [a addButtonWithTitle:@"Yes"]; [a addButtonWithTitle:@"No"]; [a addButtonWithTitle:@"I don't know"];
        for (NSButton *b in [a buttons]) [b setKeyEquivalent:@""];
        [[[a buttons] objectAtIndex:2] setKeyEquivalent:@"\r"];
    } else if ([stage isEqual:@"blocked"]) {
        [a setMessageText:@"THIS CONFIGURATION IS NOT SUPPORTED"];
        [a addButtonWithTitle:@"Quit"];
    } else if ([stage isEqual:@"final"]) {
        [a addButtonWithTitle:@"Quit"]; [a addButtonWithTitle:@"Confirm no hybrid/MBR Windows"];
        [a setShowsSuppressionButton:YES];
        [[a suppressionButton] setTitle:@"Don't ask me this again"];
        [[a suppressionButton] setState:NSOffState];
    } else if ([stage isEqual:@"guide-no"]) {
        [a addButtonWithTitle:@"Go back"]; [a addButtonWithTitle:@"I checked: no hybrid/MBR Windows"];
    } else if ([stage isEqual:@"versions"]) {
        [a addButtonWithTitle:@"Confirm selection"]; [a addButtonWithTitle:@"I don't know"];
        NSView *box=[[[NSView alloc] initWithFrame:NSMakeRect(0,0,450,196)] autorelease];
        NSArray *versions=@[@"Windows XP",@"Windows Vista",@"Windows 7",@"Windows 8",@"Windows 8.1",@"Windows 10",@"Windows 11"];
        for (NSUInteger i=0;i<[versions count];i++) {
            NSButton *b=[[[NSButton alloc] initWithFrame:NSMakeRect(0,168-i*24,440,24)] autorelease];
            [b setButtonType:NSSwitchButton]; [b setTitle:[versions objectAtIndex:i]];
            [b setState:NSOffState]; [b setTag:710+i]; [box addSubview:b];
        }
        [a setAccessoryView:box];
    } else {
        [a addButtonWithTitle:@"Back to the question"]; [a addButtonWithTitle:@"Quit"];
    }
    BoundAlertText(a,DialogSpace(),NO);
    return a;
}

static NSString *LayoutInstructions(void) {
    return @"HOW TO CHECK — STRONGLY RECOMMENDED\nOpen Terminal and run: diskutil list\nIdentify every physical drive containing an installed OS (not a disk image or synthesised APFS disk). For each, replace diskN below with its identifier, for example disk0:\n\nsudo /usr/sbin/fdisk /dev/diskN\nsudo /usr/sbin/gpt -r show /dev/diskN\n\nThese exact commands display partition information; do not add editing flags or use repair/conversion commands. In fdisk's four partition entries, a normal protective MBR has one EE entry and three unused 00 entries. EE plus any other occupied entry indicates hybrid MBR. A legacy/MBR Windows partition can use 07, 0B or 0C; those codes alone do not prove Windows is installed. A GPT label from diskutil alone DOES NOT rule out hybrid MBR. If output is unclear, inconsistent, or access is denied, DO NOT GUESS. Stop and ask for help with the output. If you still cannot establish the layout with certainty, quit this app, restart into Recovery (Command-R), open Utilities > Terminal, run csrutil disable, restart and reopen GoAwayBCA. It will run the automatic check. Temporarily disabling SIP reduces system protection. REENABLE SIP with csrutil enable in Recovery after finishing your Windows installations; you may also reenable it before installing and use these confirmations.\n\nGoAwayBCA does NOT run these checks for you and does not convert the disk. A false No can cause boot configuration changes incompatible with your layout: existing macOS or Windows systems may become UNBOOTABLE, recovery/reinstallation may be required, and data may be lost during attempted recovery. Make a complete backup before proceeding.";
}

static NSDictionary *LayoutDeclaration(NSDictionary *message) {
    NSUserDefaults *prefs=[NSUserDefaults standardUserDefaults];
    NSDictionary *saved=[prefs dictionaryForKey:LayoutPreference];
    if ([[saved objectForKey:@"confirmed"] isEqual:@YES]) {
        NSMutableDictionary *r=[[saved mutableCopy] autorelease]; [r setObject:@YES forKey:@"remembered"]; return r;
    }
    NSString *model=[message objectForKey:@"model"] ?: @"Unknown model";
    NSDictionary *modes=[message objectForKey:@"boot_modes"];
    for (;;) {
        NSInteger answer=[LayoutAlert(@"question",[NSString stringWithFormat:
            @"DO YOU CURRENTLY HAVE A HYBRID MBR SETUP OR ANY MBR/LEGACY-BOOT WINDOWS INSTALLATION?\n\nDetected Mac: %@\n\nGOAWAYBCA SUPPORTS PURE GPT/EFI ONLY. It does NOT automatically check for hybrid MBR or MBR Windows. Answer for the actual current disk layout, not just what your Mac normally supports. Choosing No incorrectly can make existing operating systems UNBOOTABLE. If unsure, choose I don't know.",model]) runModal];
        if (answer==NSAlertFirstButtonReturn) {
            [LayoutAlert(@"blocked",@"GOAWAYBCA CANNOT BE USED WITH HYBRID MBR OR MBR-BASED WINDOWS.\n\nUsing it on that layout can make existing systems UNBOOTABLE. Back up your data and obtain help restoring a pure GPT/EFI configuration before using this app. GoAwayBCA does not convert or repartition disks.") runModal];
            return nil;
        }
        if (answer!=NSAlertSecondButtonReturn && answer!=NSAlertThirdButtonReturn) return nil;
        NSInteger existing=[LayoutAlert(@"existing",@"DO YOU ALREADY HAVE WINDOWS INSTALLED ON THIS MAC?\n\nCount native installations on connected physical drives, not Windows running inside a virtual machine. No current Windows installation lowers the likelihood of a hybrid layout, but does NOT rule it out: a layout from a previous installation can remain after Windows is deleted.") runModal];
        if (existing<NSAlertFirstButtonReturn || existing>NSAlertThirdButtonReturn) return nil;
        NSMutableArray *versions=[NSMutableArray array]; BOOL versionsKnown=NO;
        NSInteger bootcamp=NSAlertThirdButtonReturn;
        if (existing==NSAlertFirstButtonReturn) {
            for (;;) {
                NSAlert *v=LayoutAlert(@"versions",@"Tick EVERY Windows version currently installed. More than one selection is allowed. If you cannot identify all of them, choose I don't know.");
                NSInteger choice=[v runModal];
                if (choice==NSAlertSecondButtonReturn) break;
                if (choice!=NSAlertFirstButtonReturn) return nil;
                for (NSInteger tag=710;tag<=716;tag++) {
                    NSButton *b=(NSButton *)[[v accessoryView] viewWithTag:tag];
                    if ([b state]==NSOnState) [versions addObject:[b title]];
                }
                if ([versions count]) { versionsKnown=YES; break; }
                NSRunAlertPanel(@"Choose your installed versions",@"Select at least one Windows version, or choose I don't know.",@"OK",nil,nil);
            }
            if ([versions containsObject:@"Windows XP"]) {
                NSRunAlertPanel(@"WINDOWS XP: LEGACY/MBR BOOT IS VERY LIKELY",@"A normal native Windows XP installation uses BIOS/legacy boot. On a GPT Mac disk, that normally means a HYBRID MBR. XP selected here is a strong warning, not a direct inspection of your disk. DO NOT CONFIRM NO WITHOUT CHECKING. A wrong answer can make existing systems UNBOOTABLE.",@"Continue to checking guidance",nil,nil);
            }
            bootcamp=[LayoutAlert(@"bootcamp",@"WAS ANY OF THAT WINDOWS INSTALLED USING BOOT CAMP ASSISTANT?\n\nAnswer Yes if at least one existing installation was created that way. Its Windows version and Mac model help explain the usual boot method; they do not prove the current partition layout.") runModal];
            if (bootcamp<NSAlertFirstButtonReturn || bootcamp>NSAlertThirdButtonReturn) return nil;
        }
        NSMutableString *risk=[NSMutableString stringWithFormat:@"Detected Mac: %@. ",model];
        if (existing==NSAlertSecondButtonReturn) [risk appendString:@"No existing Windows was reported, so a hybrid layout is less likely, but an old layout may remain.\n\n"];
        else {
            [risk appendString:bootcamp==NSAlertFirstButtonReturn ? @"Boot Camp Assistant was reported for an existing Windows installation.\n" : @"The current boot method cannot be inferred from the installation history alone.\n"];
            for (NSString *family in versions) {
                NSString *mode=[[modes objectForKey:family] objectForKey:@"status"] ?: @"unknown";
                [risk appendFormat:@"%@: recorded Apple Boot Camp method for this model: %@.\n",family,mode];
            }
            [risk appendString:@"Unknown means the records do not establish Apple's method; it does NOT mean EFI. Models and previous installation methods never prove the present layout.\n\n"];
        }
        [risk appendString:LayoutInstructions()];
        NSInteger guide=[LayoutAlert(answer==NSAlertSecondButtonReturn ? @"guide-no" : @"guide-unknown",risk) runModal];
        if (answer==NSAlertThirdButtonReturn) { if (guide==NSAlertFirstButtonReturn) continue; return nil; }
        if (guide==NSAlertFirstButtonReturn) continue;
        if (guide!=NSAlertSecondButtonReturn) return nil;
        NSAlert *final=LayoutAlert(@"final",@"FINAL CONFIRMATION\n\nI confirm this Mac has NO HYBRID MBR SETUP and NO MBR/LEGACY-BOOT WINDOWS INSTALLATION.\n\nGOAWAYBCA HAS NOT VERIFIED THIS. If my answer is wrong, proceeding can make macOS, Windows or other installed systems UNBOOTABLE. Recovery or reinstallation may be needed, with a risk of data loss. I have read the checking guidance and understand that a full backup is strongly recommended.\n\nOnly continue if you are sure. If you suppress this question and later change disks or boot layouts, re-enable it in Settings before using GoAwayBCA again.");
        if ([final runModal]!=NSAlertSecondButtonReturn) return nil;
        NSDictionary *result=@{@"confirmed":@YES,@"remembered":@NO,@"model":model,
            @"existing_windows_answer":@(existing-NSAlertFirstButtonReturn),@"versions":versions,
            @"versions_known":@(versionsKnown),@"boot_camp_answer":@(bootcamp-NSAlertFirstButtonReturn)};
        if ([[final suppressionButton] state]==NSOnState) { [prefs setObject:result forKey:LayoutPreference]; [prefs synchronize]; }
        return result;
    }
}


static NSDictionary *ReadPlist(NSString *path) {

    NSData *data = [NSData dataWithContentsOfFile:path];

    if (!data) return nil;

    id result = [NSPropertyListSerialization propertyListFromData:data

        mutabilityOption:NSPropertyListImmutable format:NULL errorDescription:NULL];

    return [result isKindOfClass:[NSDictionary class]] ? result : nil;

}

static int CompareVersions(NSString *left, NSString *right) {

    NSArray *a = [left componentsSeparatedByString:@"."], *b = [right componentsSeparatedByString:@"."];

    unsigned i;

    for (i = 0; i < 3; i++) {

        int x = i < [a count] ? [[a objectAtIndex:i] intValue] : 0;

        int y = i < [b count] ? [[b objectAtIndex:i] intValue] : 0;

        if (x != y) return x < y ? -1 : 1;

    }

    return 0;

}

static BOOL ValidVersion(NSString *version) {

    if (![version isKindOfClass:[NSString class]]) return NO;

    NSArray *parts = [version componentsSeparatedByString:@"."];

    if ([parts count] < 2 || [parts count] > 3) return NO;

    NSCharacterSet *nonDigits = [[NSCharacterSet characterSetWithCharactersInString:@"0123456789"] invertedSet];

    for (NSString *part in parts)

        if (![part length] || [part rangeOfCharacterFromSet:nonDigits].location != NSNotFound) return NO;

    return YES;

}

static NSDictionary *SelectProfile(NSArray *profiles, NSString *version, NSString *arch) {

    if (!ValidVersion(version)) return nil;

    NSArray *parts = [version componentsSeparatedByString:@"."];

    int major = [[parts objectAtIndex:0] intValue], minor = [[parts objectAtIndex:1] intValue];

    if (!((major == 10 && minor >= 7 && minor <= 15) || (major >= 11 && major <= 15) || major == 26)) return nil;

    NSDictionary *found = nil;

    for (NSDictionary *p in profiles) {

        if (!ValidVersion([p objectForKey:@"minimum"]) || !ValidVersion([p objectForKey:@"maximum"])

            || CompareVersions([p objectForKey:@"minimum"],[p objectForKey:@"maximum"]) > 0) return nil;

        if ([[p objectForKey:@"available"] boolValue] && [[p objectForKey:@"architectures"] containsObject:arch]

            && CompareVersions(version, [p objectForKey:@"minimum"]) >= 0

            && CompareVersions(version, [p objectForKey:@"maximum"]) <= 0) {

            if (found) return nil;

            found = p;

        }

    }

    return found;

}

static NSString *Digest(NSString *path) {

    NSFileHandle *f = [NSFileHandle fileHandleForReadingAtPath:path];

    if (!f) return nil;

    CC_SHA256_CTX ctx; CC_SHA256_Init(&ctx);

    NSData *data;

    while ([(data = [f readDataOfLength:1024*1024]) length])

        CC_SHA256_Update(&ctx, [data bytes], (CC_LONG)[data length]);

    [f closeFile];

    unsigned char digest[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(digest, &ctx);

    NSMutableString *text = [NSMutableString string];

    unsigned i; for (i=0; i<sizeof(digest); i++) [text appendFormat:@"%02x", digest[i]];

    return text;

}

static BOOL VerifyProfileReporting(NSDictionary *profile, BOOL management, id reporter) {

    NSDictionary *files = [profile objectForKey:@"files"];

    if (![files count]) return NO;

    NSString *root = [Resources stringByResolvingSymlinksInPath];

    NSUInteger total = 0, verified = 0;

    NSTimeInterval lastUpdate = 0;

    for (NSString *name in files)

        if (!(management && ([name hasPrefix:@"private-win7-media/"] || [name hasPrefix:@"private-win7-delta/"] || [name hasPrefix:@"community-drivers/"]))) total++;

    for (NSString *relative in files) {

        if ([relative isAbsolutePath] || [[relative pathComponents] containsObject:@".."]

            || [relative rangeOfString:@"\\"].location != NSNotFound) return NO;

        if (management && ([relative hasPrefix:@"private-win7-media/"] ||

                           [relative hasPrefix:@"private-win7-delta/"] ||

                           [relative hasPrefix:@"community-drivers/"])) continue;

        NSString *path = [[root stringByAppendingPathComponent:relative] stringByResolvingSymlinksInPath];

        if (![path hasPrefix:[root stringByAppendingString:@"/"]]

            || ![[files objectForKey:relative] isEqual:Digest(path)]) return NO;

        verified++;

        NSTimeInterval now = [NSDate timeIntervalSinceReferenceDate];

        if (reporter && (verified == total || now-lastUpdate >= 0.4)) {

            double percent = total ? 100.0*verified/total : 100.0;

            NSDictionary *progress = [NSDictionary dictionaryWithObjectsAndKeys:

                @"Verifying app before authorization",@"phase",@YES,@"determinate",@(percent),@"percent",nil];

            NSDictionary *event = [NSDictionary dictionaryWithObjectsAndKeys:@"log",@"type",progress,@"progress",

                [NSString stringWithFormat:@"Verifying app: %lu of %lu files verified (%.1f%%)",(unsigned long)verified,(unsigned long)total,percent],@"text",nil];

            [reporter performSelectorOnMainThread:@selector(handleMessage:) withObject:event waitUntilDone:YES];

            lastUpdate = now;

        }

    }

    for (NSString *key in [NSArray arrayWithObjects:@"python", @"worker", @"wim", @"mkntfs", @"ntfscat", nil])

        if (![files objectForKey:[profile objectForKey:key]]) return NO;

    return YES;

}

static BOOL VerifyProfileForOperation(NSDictionary *profile, BOOL management) { return VerifyProfileReporting(profile,management,nil); }

static BOOL VerifyProfile(NSDictionary *profile) { return VerifyProfileForOperation(profile, NO); }

static BOOL ReadAll(int fd, void *buffer, size_t length) {

    size_t offset = 0;

    while (offset < length) {

        ssize_t n = read(fd, (char *)buffer + offset, length - offset);

        if (n < 0 && errno == EINTR) continue;

        if (n <= 0) return NO;

        offset += n;

    }

    return YES;

}

static BOOL WriteAll(int fd, const void *buffer, size_t length) {

    size_t offset = 0;

    while (offset < length) {

        ssize_t n = write(fd, (char *)buffer + offset, length - offset);

        if (n < 0 && errno == EINTR) continue;

        if (n <= 0) return NO;

        offset += n;

    }

    return YES;

}

static NSDictionary *Receive(int fd) {

    uint32_t size;

    if (!ReadAll(fd, &size, sizeof(size))) return nil;

    size = ntohl(size);

    if (!size || size > 2*1024*1024) return nil;

    NSMutableData *data = [NSMutableData dataWithLength:size];

    if (!ReadAll(fd, [data mutableBytes], size)) return nil;

    id message = [NSPropertyListSerialization propertyListFromData:data

        mutabilityOption:NSPropertyListImmutable format:NULL errorDescription:NULL];

    return [message isKindOfClass:[NSDictionary class]] ? message : nil;

}

static BOOL Send(int fd, NSDictionary *message) {

    NSData *data = [NSPropertyListSerialization dataFromPropertyList:message

        format:NSPropertyListXMLFormat_v1_0 errorDescription:NULL];

    if (![data length] || [data length] > 2*1024*1024) return NO;

    uint32_t size = htonl((uint32_t)[data length]);

    return WriteAll(fd, &size, 4) && WriteAll(fd, [data bytes], [data length]);

}

static NSString *ShellQuote(NSString *s) {

    return [NSString stringWithFormat:@"'%@'", [s stringByReplacingOccurrencesOfString:@"'" withString:@"'\\''"]];

}

static NSString *AppleScriptQuote(NSString *s) {

    return [NSString stringWithFormat:@"\"%@\"", [[s stringByReplacingOccurrencesOfString:@"\\" withString:@"\\\\"]

        stringByReplacingOccurrencesOfString:@"\"" withString:@"\\\""]];

}

static NSTextField *Label(NSView *parent, NSString *text, NSRect frame, BOOL bold) {

    NSTextField *v = [[[NSTextField alloc] initWithFrame:frame] autorelease];

    [v setStringValue:text]; [v setEditable:NO]; [v setSelectable:YES];

    [v setBordered:NO]; [v setDrawsBackground:NO];

    [v setFont:bold ? [NSFont boldSystemFontOfSize:15] : [NSFont systemFontOfSize:12]];

    [parent addSubview:v]; return v;

}



@interface DeployerUI : NSObject {

    NSWindow *window;

    NSTextField *isoField, *preparedField, *status;

    NSTextView *logs;

    NSButton *startButton, *isoButton, *preparedButton, *choiceContinue, *manageButton, *rebuildButton, *skipVerificationButton, *savedDriversButton;

    NSButton *compatibilityButton, *diagnosticsButton, *efiAccessButton, *debugButton, *savedFilesButton, *troubleshootingButton;

    NSProgressIndicator *activity;

    NSDictionary *configuration, *profile;

    NSString *sessionPath, *token, *workerOperation, *verificationToken;

    int listener, peer;

    BOOL busy;
    BOOL startupSucceeded;

}

- (void)buildWindow;

- (void)layoutActionRow;

- (void)appendLog:(NSString *)text;

- (void)updateProgress:(NSDictionary *)progress;

- (void)handleMessage:(NSDictionary *)message;

- (NSAlert *)alertForMessage:(NSDictionary *)message;

- (NSAlert *)machineAlert;

- (void)savePreview:(NSString *)path;

- (void)selectionChanged:(NSPopUpButton *)sender;

@end



@interface DeployerCanvas : NSView

@end

@implementation DeployerCanvas

- (BOOL)isOpaque { return YES; }

- (void)drawRect:(NSRect)rect {

    [[NSColor windowBackgroundColor] setFill];

    NSRectFill(rect);

}

@end



@implementation DeployerUI

- (NSButton *)button:(NSString *)title frame:(NSRect)frame action:(SEL)selector {

    NSButton *b = [[[NSButton alloc] initWithFrame:frame] autorelease];

    [b setTitle:title]; [b setBezelStyle:NSRoundedBezelStyle]; [b setTarget:self]; [b setAction:selector];

    [[window contentView] addSubview:b]; return b;

}

- (void)buildWindow {

    listener = peer = -1;

    configuration = [ReadPlist([Resources stringByAppendingPathComponent:@"runtime-profiles.plist"]) retain];

    NSString *version = [ReadPlist(@"/System/Library/CoreServices/SystemVersion.plist") objectForKey:@"ProductVersion"];

    struct utsname host; uname(&host);

    profile = [SelectProfile([configuration objectForKey:@"profiles"], version,

                            [NSString stringWithUTF8String:host.machine]) retain];

    window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,720,540)

        styleMask:NSTitledWindowMask|NSClosableWindowMask|NSMiniaturizableWindowMask|NSResizableWindowMask

        backing:NSBackingStoreBuffered defer:NO];

    [window setTitle:DemoMode ? @"GoAwayBCA — UI simulation (no disks accessed)" :

        ([[configuration objectForKey:@"installation_enabled"] boolValue] ?

            @"GoAwayBCA — Beta 1" : @"GoAwayBCA — installation unavailable")];

    [window setReleasedWhenClosed:NO]; [window setDelegate:(id)self];

    [window setContentView:[[[DeployerCanvas alloc] initWithFrame:NSMakeRect(0,0,720,540)] autorelease]];

    [window setContentMinSize:NSMakeSize(640,500)];

    /* Inherit the host appearance. Cocoa supplies that OS's controls, colours,

     * fonts and (where supported) the user's Light/Dark Mode. */

    NSView *view = [window contentView];

    Label(view, @"Install and manage Windows on your Intel Mac", NSMakeRect(20,495,560,26), YES);

    NSButton *support = [self button:@"Support…" frame:NSMakeRect(600,493,100,30) action:@selector(showSupport:)];

    Label(view, [NSString stringWithFormat:@"%@ • Internal exFAT or NTFS partitions", HardwareModel()],

          NSMakeRect(20,470,440,22), NO);

    troubleshootingButton = [self button:@"Settings…" frame:NSMakeRect(470,465,230,30) action:@selector(showSettings:)];

    [troubleshootingButton setAutoresizingMask:NSViewMinXMargin|NSViewMinYMargin];

    Label(view, @"Windows 7/8 support is being actively developed for a future update.", NSMakeRect(20,439,680,22), YES);

    isoField = Label(view, @"Choose a Windows 8.1, 10 or 11 ISO… Windows 7/8 are not yet supported.", NSMakeRect(20,412,540,24), NO);

    isoButton = [self button:@"Choose ISO…" frame:NSMakeRect(570,409,130,30) action:@selector(chooseISO:)];

    savedDriversButton = [self button:@"Open Drivers Folder" frame:NSMakeRect(475,312,225,30) action:@selector(showSavedDrivers:)];

    preparedField = Label(view, @"This program does not include any partitioning functionality. You must create the partitions yourself before using this tool. This tool only formats selected partitions after multiple confirmations",

                          NSMakeRect(20,347,680,57), NO);

    [[preparedField cell] setWraps:YES]; [[preparedField cell] setScrollable:NO];

    preparedButton = [self button:@"Choose folder…" frame:NSMakeRect(570,349,130,30) action:@selector(choosePrepared:)];

    [preparedButton setHidden:YES];

    rebuildButton = [self button:@"Rebuild Boot Menu…" frame:NSMakeRect(247,312,222,30) action:@selector(rebuild:)];

    [rebuildButton setEnabled:profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    compatibilityButton = [self button:@"Mac compatibility…" frame:NSMakeRect(20,278,220,30) action:@selector(showMachine:)];

    diagnosticsButton = [self button:@"Collect Hardware Report…" frame:NSMakeRect(247,278,222,30) action:@selector(diagnostics:)];

    efiAccessButton = [self button:@"Mount / Unmount EFI…" frame:NSMakeRect(475,278,225,30) action:@selector(efiAccess:)];

    [diagnosticsButton setEnabled:profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    [efiAccessButton setEnabled:profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    NSTextField *uninstallWarning = Label(view,@"Use Manage Installations to remove Windows. Formatting it manually can leave boot files and require repair. OpenCore will not be replaced.",NSMakeRect(20,233,680,40),NO);

    [[uninstallWarning cell] setWraps:YES]; [[uninstallWarning cell] setScrollable:NO];

    status = Label(view, @"", NSMakeRect(20,193,505,37), NO);

    skipVerificationButton = [self button:@"Skip verification" frame:NSMakeRect(535,198,165,30) action:@selector(skipVerification:)];

    [skipVerificationButton setEnabled:NO];

    [skipVerificationButton setToolTip:@"Skip only the current optional read-only check. Partition checks, source authentication and boot-file protections cannot be skipped."];

    NSString *message = profile ? [NSString stringWithFormat:@"macOS %@ (%s). Ready to choose a Windows ISO.",

                                   version, host.machine]

                               : @"No validated runtime for this host. Installation is unavailable; no fallback will be guessed.";

    [status setStringValue:message];

    activity = [[NSProgressIndicator alloc] initWithFrame:NSMakeRect(20,186,680,5)];

    [activity setIndeterminate:YES]; [activity setDisplayedWhenStopped:NO]; [view addSubview:activity];

    NSScrollView *scroll = [[[NSScrollView alloc] initWithFrame:NSMakeRect(20,80,680,103)] autorelease];

    [scroll setHasVerticalScroller:YES]; [scroll setBorderType:NSBezelBorder];

    logs = [[NSTextView alloc] initWithFrame:NSMakeRect(0,0,660,103)];

    [logs setEditable:NO]; [logs setFont:[NSFont userFixedPitchFontOfSize:11]];

    [logs setAutoresizingMask:NSViewWidthSizable];

    [[logs textContainer] setWidthTracksTextView:YES];

    [scroll setDocumentView:logs]; [view addSubview:scroll];

    startButton = [self button:DemoMode ? @"Run safe UI simulation" : @"Review and prepare…"

                         frame:NSMakeRect(455,15,245,36) action:@selector(start:)];

    [startButton setEnabled:DemoMode || (profile && [[configuration objectForKey:@"installation_enabled"] boolValue])];

    NSButton *save = [self button:@"Save Log…" frame:NSMakeRect(20,15,110,36) action:@selector(saveLog:)];

    [save setTag:710];

    savedFilesButton = [self button:@"Saved Files…" frame:NSMakeRect(280,15,134,36) action:@selector(savedFiles:)];

    [savedFilesButton setToolTip:@"Review saved downloads, driver caches and preparation files; choose what to delete. Nothing is deleted automatically."];

    [savedFilesButton setEnabled:profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    debugButton = [self button:@"Delete Boot Menu…" frame:NSMakeRect(140,15,210,36) action:@selector(debugReset:)];

    [debugButton setToolTip:@"Delete this app's Windows boot menu only when no Windows installations remain. Three confirmations and an EFI backup are required."];

    [debugButton setEnabled:profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    manageButton = [self button:@"Manage Installations…" frame:NSMakeRect(20,312,222,30) action:@selector(manage:)];

    [manageButton setEnabled:profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    NSTextField *confirmation = Label(view, @"Formatting requires two confirmations; then type confirm.", NSMakeRect(20,55,680,22), NO);

    for (NSView *child in [view subviews]) [child setAutoresizingMask:NSViewWidthSizable|NSViewMinYMargin];

    for (NSView *child in [NSArray arrayWithObjects:support,isoButton,preparedButton,skipVerificationButton,nil])

        [child setAutoresizingMask:NSViewMinXMargin|NSViewMinYMargin];

    for (NSButton *button in [NSArray arrayWithObjects:compatibilityButton,diagnosticsButton,efiAccessButton,nil])

        [button setAutoresizingMask:NSViewMinYMargin];

    [scroll setAutoresizingMask:NSViewWidthSizable|NSViewHeightSizable];

    [startButton setAutoresizingMask:NSViewMinXMargin|NSViewMaxYMargin];

    [save setAutoresizingMask:NSViewMaxXMargin|NSViewMaxYMargin];

    [debugButton setAutoresizingMask:NSViewMaxXMargin|NSViewMaxYMargin];

    [savedFilesButton setAutoresizingMask:NSViewMaxXMargin|NSViewMaxYMargin];

    [confirmation setAutoresizingMask:NSViewWidthSizable|NSViewMaxYMargin];

    [manageButton setAutoresizingMask:NSViewMinYMargin];

    [rebuildButton setAutoresizingMask:NSViewMinYMargin];

    [savedDriversButton setAutoresizingMask:NSViewMinYMargin];

    [self appendLog:@"GoAwayBCA Beta 1. Choose a Windows 8.1, 10 or 11 ISO to begin."];

    if (![[configuration objectForKey:@"installation_enabled"] boolValue])

        [self appendLog:@"Installation is unavailable in this configuration."];

    [window center];

    if (!SelfTest && !DemoMode) {

        [window setFrameUsingName:WindowFramePreference];

        NSScreen *screen = [window screen] ?: [NSScreen mainScreen];

        [window setFrame:FitWindowFrame([window frame],[screen visibleFrame]) display:NO];

        [window setFrameAutosaveName:WindowFramePreference];

    }

    [self layoutActionRow];

}

- (void)layoutActionRow {

    CGFloat width = NSWidth([[window contentView] bounds]);

    [[[window contentView] viewWithTag:710] setFrame:NSMakeRect(20,15,90,36)];

    [debugButton setFrame:NSMakeRect(116,15,150,36)];

    [savedFilesButton setFrame:NSMakeRect(272,15,134,36)];

    [startButton setFrame:NSMakeRect(412,15,width-432,36)];

    CGFloat column = (width-52)/3.0;

    NSArray *buttons = [NSArray arrayWithObjects:manageButton,rebuildButton,savedDriversButton,compatibilityButton,diagnosticsButton,efiAccessButton,nil];

    NSUInteger index = 0;

    for (NSButton *button in buttons) {

        NSRect frame = [button frame]; frame.origin.x = 20+(index%3)*(column+6); frame.size.width=column;

        [button setFrame:frame]; index++;

    }

}

- (void)windowDidResize:(NSNotification *)notification { [self layoutActionRow]; }

- (void)applicationDidFinishLaunching:(NSNotification *)notification {

    [self buildWindow]; [window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES];

    if (!SelfTest && !DemoMode) {
        [workerOperation release]; workerOperation=[@"startup-check" copy];
        startupSucceeded=NO;
        [self setBusy:YES];
        [self setLaunchControlsEnabled:NO inView:[window contentView]];
        [self appendLog:@"Administrator authorization is needed once for this app session. Checking SIP and selecting the launch disk-layout route."];
        [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];
    }
}

- (void)setLaunchControlsEnabled:(BOOL)enabled inView:(NSView *)view {
    for (NSView *child in [view subviews]) {
        if ([child isKindOfClass:[NSControl class]]) [(NSControl *)child setEnabled:enabled];
        [self setLaunchControlsEnabled:enabled inView:child];
    }
}

- (void)showLaunchGuidance {
    NSString *model = HardwareModel();

    if (!SelfTest && !DemoMode && ![[NSUserDefaults standardUserDefaults] boolForKey:@"InstallationGuidanceSuppressed-v2"]) {
        NSAlert *guide = [self alertForMessage:@{@"type":@"notice", @"text":@"We recommend an official, unmodified Windows ISO and checking its hash yourself against a trusted reference. GoAwayBCA does not enforce this recommendation.\n\nIf you plan to install multiple Windows versions, we recommend completing one installation before starting the next. Boot into Windows, finish setup, install its drivers and complete required restarts. Windows normally updates its boot files and settings during setup, so waiting lets that installation's boot configuration settle before another version is added. This may not be necessary in every case, but it is the recommended approach.\n\nLinux coexistence has not been tested in any installation order. See Mac compatibility for details and reporting guidance.", @"button_title":@"Done"}];
        [guide setShowsSuppressionButton:YES];
        [[guide suppressionButton] setTitle:@"Do not show this message again"];
        [[guide suppressionButton] setState:NSOffState];
        [guide runModal];
        if ([[guide suppressionButton] state]==NSOnState)
            [[NSUserDefaults standardUserDefaults] setBool:YES forKey:@"InstallationGuidanceSuppressed-v2"];
    }

    if (!SelfTest && !DemoMode && ![[NSUserDefaults standardUserDefaults] boolForKey:BackupWarningPreference]) {

        NSAlert *alert = BackupWarning();

        NSInteger response = [alert runModal];

        if (RememberBackupWarningChoice(response, [[alert suppressionButton] state]))

            [[NSUserDefaults standardUserDefaults] setBool:YES forKey:BackupWarningPreference];

        if (response != NSAlertFirstButtonReturn) { [NSApp terminate:nil]; return; }

    }

    /* One launch-only advisory. This preference is separate from the backup

     * reminder and is never consulted by a formatting confirmation. */

    if (!SelfTest && !DemoMode && ShouldShowSecurityChipWarning(model,

            [[NSUserDefaults standardUserDefaults] boolForKey:SecurityChipWarningPreference])) {

        NSAlert *alert = SecurityChipWarning(model);

        NSInteger response = [alert runModal];

        if (RememberSecurityChipWarningChoice(response, [[alert suppressionButton] state]))

            [[NSUserDefaults standardUserDefaults] setBool:YES forKey:SecurityChipWarningPreference];

        if (response != NSAlertFirstButtonReturn) { [NSApp terminate:nil]; return; }

    }

}

- (NSAlert *)machineAlert {

    NSString *model = HardwareModel();

    NSDictionary *os = ReadPlist(@"/System/Library/CoreServices/SystemVersion.plist");

    struct utsname host; memset(&host,0,sizeof(host)); uname(&host);

    uint64_t memory = 0; size_t size = sizeof(memory);

    BOOL knownMemory = sysctlbyname("hw.memsize",&memory,&size,NULL,0) == 0 && size == sizeof(memory) && memory > 0;

    NSString *memoryText = knownMemory ? [NSString stringWithFormat:@"%.1f GiB", memory / (1024.0*1024.0*1024.0)] : @"Unavailable";

    NSString *runtime = profile ? [profile objectForKey:@"id"] : @"Unavailable for this host — installation disabled";

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    [alert setMessageText:@"Your Mac and Windows compatibility"];

    [alert setInformativeText:[NSString stringWithFormat:@"Model identifier: %@\nmacOS: %@ (build %@)\nArchitecture: %s\nMemory: %@\nBundled runtime: %@\n\n%@\n\nThis is the model reported by macOS; bootloader model spoofing can change it. No serial number is collected.\n\nApp-host compatibility, Windows EFI boot and Windows drivers are separate questions. The installer offers optional driver preparation from macOS before formatting, including a model override for drivers. Fresh Windows may have no networking. See DRIVER-GUIDANCE.txt for details and the separate Brigadier reference.\n\nThis summary does not inspect, change or certify your disk layout. A backup and both formatting confirmations are still required.",

        model, [os objectForKey:@"ProductVersion"] ?: @"Unavailable",

        [os objectForKey:@"ProductBuildVersion"] ?: @"Unavailable", host.machine, memoryText, runtime, ModelGuidance(model)]];

    [alert addButtonWithTitle:@"OK"];

    [alert setAccessoryView:[[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,1)] autorelease]];

    return alert;

}

- (void)showMachine:(id)sender {
    NSAlert *alert=[self machineAlert];
    BoundAlertText(alert,DialogSpace(),NO);
    [alert runModal];
}

- (void)showSupport:(id)sender {

    if (SelfTest || DemoMode) return; /* QA never opens the browser. */

    NSString *destination = SupportURL([SupportAlert() runModal]);

    if (destination && ![[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:destination]])

        NSRunAlertPanel(@"Could not open support link", @"Open this address in your browser:\n%@", @"OK", nil, nil, destination);

}

- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)app { return YES; }

- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {

    if (busy) {

        NSRunAlertPanel(@"Operation still active", @"Keep the app open while preparation or deployment is running. Do not interrupt disk writes.", @"Keep open", nil, nil);

        return NSTerminateCancel;

    }

    if (peer >= 0) { shutdown(peer,SHUT_RDWR); close(peer); peer=-1; }
    return NSTerminateNow;

}

- (BOOL)windowShouldClose:(id)sender { return !busy; }

- (void)appendLog:(NSString *)text {

    NSTextStorage *storage = [logs textStorage];

    NSDictionary *attributes = [NSDictionary dictionaryWithObjectsAndKeys:[NSFont userFixedPitchFontOfSize:11],NSFontAttributeName,

        [NSColor textColor],NSForegroundColorAttributeName,nil];

    [storage appendAttributedString:[[[NSAttributedString alloc] initWithString:[text stringByAppendingString:@"\n"] attributes:attributes] autorelease]];

    if ([storage length] > 300000) [storage deleteCharactersInRange:NSMakeRange(0, [storage length]-250000)];

    [logs scrollRangeToVisible:NSMakeRange([storage length],0)];

}

- (void)saveLog:(id)sender {

    /* Export the visible progress log as the ordinary GUI user. Do not widen

     * permissions on the administrator's full receipt/EFI-backup directory. */

    NSSavePanel *panel = [NSSavePanel savePanel];

    [panel setTitle:@"Save visible progress log"];

    [panel setNameFieldStringValue:@"Windows-Deployer-Log.txt"];

    if ([panel runModal] != NSFileHandlingPanelOKButton) return;

    NSError *error = nil;

    NSString *text = [@"Visible progress log. Full receipts and EFI backups remain in the session path shown below.\n\n"

                      stringByAppendingString:[logs string]];

    if (![text writeToFile:[[panel URL] path] atomically:YES encoding:NSUTF8StringEncoding error:&error])

        NSRunAlertPanel(@"Log could not be saved", @"%@", @"OK", nil, nil, [error localizedDescription]);

}

- (void)chooseISO:(id)sender {

    NSOpenPanel *panel = [NSOpenPanel openPanel];

    [panel setCanChooseDirectories:NO]; [panel setCanChooseFiles:YES]; [panel setAllowsMultipleSelection:NO];

    if ([panel runModalForDirectory:nil file:nil types:[NSArray arrayWithObject:@"iso"]] == NSOKButton)

        [isoField setStringValue:[[panel filenames] objectAtIndex:0]];

}

- (void)choosePrepared:(id)sender {

    NSOpenPanel *panel = [NSOpenPanel openPanel];

    [panel setCanChooseDirectories:YES]; [panel setCanChooseFiles:NO]; [panel setAllowsMultipleSelection:NO];

    if ([panel runModalForDirectory:nil file:nil types:nil] == NSOKButton)

        [preparedField setStringValue:[[panel filenames] objectAtIndex:0]];

}

- (void)showSavedDrivers:(id)sender {

    if (SelfTest || DemoMode) return;

    NSString *path=[NSHomeDirectory() stringByAppendingPathComponent:@"Library/Application Support/BackToSeven/Drivers"];

    NSError *error=nil;

    if (![[NSFileManager defaultManager] createDirectoryAtPath:path withIntermediateDirectories:YES attributes:nil error:&error]) {

        NSRunAlertPanel(@"Saved drivers",@"%@",@"OK",nil,nil,[error localizedDescription]); return;

    }

    [[NSWorkspace sharedWorkspace] openFile:path];

}

- (void)setBusy:(BOOL)value {

    busy = value; [isoButton setEnabled:!value]; [preparedButton setEnabled:!value];

    [skipVerificationButton setEnabled:NO];

    [verificationToken release]; verificationToken=nil;

    if (value) {

        [activity setIndeterminate:YES]; [activity setDisplayedWhenStopped:YES];

        [activity startAnimation:nil]; [status setStringValue:@"Preparing operation…"];

    } else {

        [activity stopAnimation:nil]; [activity setIndeterminate:NO];

        [activity setDoubleValue:0]; [activity setDisplayedWhenStopped:NO];

    }

    [startButton setEnabled:!value && (DemoMode || (profile && [[configuration objectForKey:@"installation_enabled"] boolValue]))];

    [manageButton setEnabled:!value && profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    [rebuildButton setEnabled:!value && profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    [diagnosticsButton setEnabled:!value && profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    [efiAccessButton setEnabled:!value && profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    [debugButton setEnabled:!value && profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

    [troubleshootingButton setEnabled:!value];

    [savedFilesButton setEnabled:!value && profile && [[configuration objectForKey:@"management_enabled"] boolValue]];

}

- (void)skipVerification:(id)sender {

    if (!busy || !verificationToken || peer < 0 || SelfTest || DemoMode) return;

    Send(peer,[NSDictionary dictionaryWithObjectsAndKeys:@"skip_verification",@"type",verificationToken,@"phase_token",nil]);

    [skipVerificationButton setEnabled:NO];

    [self appendLog:@"Requested skipping this verification phase. Waiting for its read-only checks to stop safely…"];

}

- (void)manage:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"manage" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)savedFiles:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"saved-files" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)experimental:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"experimental" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)rebuild:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"rebuild" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)diagnostics:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"diagnostics" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)efiAccess:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"efi-access" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)debugReset:(id)sender {

    if (SelfTest || DemoMode || busy || !profile || ![[configuration objectForKey:@"management_enabled"] boolValue]) return;

    [workerOperation release]; workerOperation = [@"reset-menu" copy];

    [self appendLog:@"Delete Windows Boot Menu: available only with no Windows installations. Three confirmations and a verified backup are required."];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)finish:(NSString *)message {

    [self appendLog:message];
    if ([workerOperation isEqual:@"startup-check"]) {
        if (!startupSucceeded) {
            NSAlert *blocked=[[[NSAlert alloc] init] autorelease];
            [blocked setMessageText:[message hasPrefix:@"THIS CONFIGURATION IS NOT SUPPORTED"] ? @"GOAWAYBCA CANNOT OPEN" : @"GoAwayBCA cannot open"];
            [blocked setInformativeText:message ?: @"Disk compatibility could not be checked."];
            [blocked addButtonWithTitle:@"Quit"];
            [blocked runModal]; [self setBusy:NO]; [NSApp terminate:nil]; return;
        }
        [self setLaunchControlsEnabled:YES inView:[window contentView]];
        [self setBusy:NO];
        [self showLaunchGuidance];
    } else [self setBusy:NO];

    [status setStringValue:message ?: @"Operation ended. See the log."];

}

- (void)updateProgress:(NSDictionary *)progress {

    NSString *label = [progress objectForKey:@"phase"];

    if (![label isKindOfClass:[NSString class]]) return;

    [activity stopAnimation:nil];

    BOOL measured = [[progress objectForKey:@"determinate"] boolValue];

    id number = [progress objectForKey:@"percent"];

    if (measured && (![number isKindOfClass:[NSNumber class]] ||

        !isfinite([number doubleValue]) || [number doubleValue] < 0 || [number doubleValue] > 100)) return;

    [activity setDisplayedWhenStopped:YES];

    [activity setIndeterminate:!measured];

    if (measured) {

        [activity setMinValue:0]; [activity setMaxValue:100];

        [activity setDoubleValue:[number doubleValue]];

        [status setStringValue:[NSString stringWithFormat:@"%@ — %.1f%%",label,[number doubleValue]]];

    } else {

        [status setStringValue:label]; [activity startAnimation:nil];

    }

    [activity setNeedsDisplay:YES];

}

- (void)start:(id)sender {

    if (SelfTest) return; /* QA must never enter the privileged deployment path. */

    if (busy) return;

    if (DemoMode) {

        [self setBusy:YES];

        [self appendLog:@"SIMULATION ONLY: no ISO, block device, mount or administrator command is opened."];

        [self handleMessage:[NSDictionary dictionaryWithObjectsAndKeys:@"choose", @"type", @"Choose a simulated edition:", @"text",

            [NSArray arrayWithObjects:@"Windows 7 Professional (fixture)", @"Windows 7 Ultimate (fixture)", nil], @"options", @1, @"sequence", nil]];

        [self finish:@"Simulation complete. Nothing was installed or modified."]; return;

    }

    if (!profile || ![[configuration objectForKey:@"installation_enabled"] boolValue]) return;

    if (![[configuration objectForKey:@"workflow"] isEqualToString:@"goawaybca"]) {

        NSRunAlertPanel(@"Build configuration mismatch", @"This GoAwayBCA interface requires its matching installation engine. No installation started.", @"OK", nil, nil); return;

    }

    if (![[isoField stringValue] isAbsolutePath]) {

        NSRunAlertPanel(@"Choose source files", @"Select your Windows ISO first.", @"OK", nil, nil); return;

    }

    [workerOperation release]; workerOperation = [@"install" copy];

    [self setBusy:YES]; [NSThread detachNewThreadSelector:@selector(beginWorker:) toTarget:self withObject:nil];

}

- (void)beginWorker:(id)unused {

    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];

    NSString *completion = nil;

    BOOL keepSession = NO;

    int authorizationChannel[2] = {-1,-1};

    @try {

        if (peer < 0) {
        if (startupSucceeded && !SelfTest) [NSException raise:@"IPC" format:@"The administrator session ended. Close and reopen GoAwayBCA before trying again."];

        if (!VerifyProfileReporting(profile,![workerOperation isEqual:@"install"],self))

            [NSException raise:@"Integrity" format:@"A bundled dependency failed verification."];

        char dir[] = "/private/tmp/windows-deployer-gui-XXXXXXXX";

        if (!mkdtemp(dir) || chmod(dir,0700)) [NSException raise:@"IPC" format:@"Cannot create private GUI session."];

        [sessionPath release]; sessionPath = [[NSString stringWithUTF8String:dir] retain];

        unsigned char secret[32]; int random = open("/dev/urandom",O_RDONLY);

        BOOL randomOK = random >= 0 && ReadAll(random,secret,sizeof(secret));

        if (random >= 0) close(random);

        if (!randomOK) [NSException raise:@"IPC" format:@"Cannot create handshake."];

        NSMutableString *text = [NSMutableString string];

        unsigned i; for(i=0;i<sizeof(secret);i++) [text appendFormat:@"%02x",secret[i]];

        [token release]; token = [text copy];

        listener = socket(AF_UNIX, SOCK_STREAM, 0);

        struct sockaddr_un address; memset(&address,0,sizeof(address)); address.sun_family=AF_UNIX;

        NSString *socketPath = [sessionPath stringByAppendingPathComponent:@"control.sock"];

        strlcpy(address.sun_path,[socketPath fileSystemRepresentation],sizeof(address.sun_path));

        if (listener < 0 || bind(listener,(struct sockaddr *)&address,sizeof(address)) || chmod(address.sun_path,0600) || listen(listener,1))

            [NSException raise:@"IPC" format:@"Cannot open private GUI channel."];

        if (NMBMakeAuthorizationChannel(authorizationChannel))

            [NSException raise:@"IPC" format:@"Cannot open administrator notification channel."];

        NSDictionary *launch = [NSDictionary dictionaryWithObjectsAndKeys:

            socketPath,@"socket",sessionPath,@"session",token,@"token",profile,@"profile",workerOperation,@"operation",

            [NSNumber numberWithInt:authorizationChannel[1]],@"authorization_writer",nil];

        if (SelfTest) {

            /* Exercise the real cancellation/cleanup/UI path without ever

             * launching osascript, a privileged worker, or a disk operation. */

            NMBFinishAuthorizationChannel(authorizationChannel[1],'C');

        } else [NSThread detachNewThreadSelector:@selector(authorizeWorker:) toTarget:self withObject:launch];

        authorizationChannel[1]=-1; /* ownership transferred to authorization */

        int authorizationResult=NMBWaitForAdministrator(listener,authorizationChannel[0],300);

        if (authorizationResult==NMBAuthCancelled)

            [NSException raise:@"Authorization" format:@"Administrator authorization cancelled. Nothing started. You can try again."];

        if (authorizationResult==NMBAuthFailed)

            [NSException raise:@"Authorization" format:@"Administrator authorization failed before the worker connected. Nothing started. You can try again."];

        if (authorizationResult!=NMBAuthConnected)

            [NSException raise:@"IPC" format:@"Administrator connection failed or timed out; nothing started."];

        close(authorizationChannel[0]); authorizationChannel[0]=-1;

        peer = accept(listener,NULL,NULL); uid_t uid; gid_t gid;

        if (peer < 0 || getpeereid(peer,&uid,&gid) || uid != 0) [NSException raise:@"IPC" format:@"Administrator peer identity failed."];

        NSDictionary *hello = Receive(peer);

        if (![[hello objectForKey:@"type"] isEqual:@"hello"] || ![[hello objectForKey:@"token"] isEqual:token]

            || [[hello objectForKey:@"protocol"] intValue] != 1) [NSException raise:@"IPC" format:@"Invalid worker handshake."];

        close(listener); listener=-1;
        }

        [self performSelectorOnMainThread:@selector(sendConfiguration:) withObject:nil waitUntilDone:YES];

        NSDictionary *event;

        BOOL complete = NO;

        while ((event = Receive(peer))) {

            if ([[event objectForKey:@"type"] isEqual:@"done"]) {

                completion = [[event objectForKey:@"text"] copy];
                if ([workerOperation isEqual:@"startup-check"]) startupSucceeded=[[event objectForKey:@"success"] boolValue];
                keepSession = [[event objectForKey:@"session_alive"] boolValue];
                complete = YES; break;

            }

            [self performSelectorOnMainThread:@selector(handleMessage:) withObject:event waitUntilDone:YES];

        }

        if (!complete) [NSException raise:@"IPC" format:@"Worker connection ended unexpectedly. Inspect saved logs before retrying."];

    } @catch (NSException *error) {

        completion = [[error reason] copy];

    }

    if (authorizationChannel[0]>=0) close(authorizationChannel[0]);

    if (authorizationChannel[1]>=0) close(authorizationChannel[1]);

    if (!keepSession && peer >= 0) { close(peer); peer = -1; }

    if (listener >= 0) { close(listener); listener = -1; }

    [self performSelectorOnMainThread:@selector(finish:)

        withObject:completion ? completion : @"Worker finished. Inspect saved logs." waitUntilDone:YES];

    [completion release];

    /* Keep the small owned session/log folder. Never delete source/mount paths. */

    [pool drain];

}

- (void)showSettings:(id)sender {
    if (busy) return;
    NSAlert *alert = [[[NSAlert alloc] init] autorelease];
    [alert setMessageText:@"Settings"];
    [alert setInformativeText:@"Reset “Don’t show again” choices to show messages again that you previously chose not to display. Messages will return when you next reach the relevant step, including at the next launch. Your window size, saved files and partition detection preference are kept."];
    [alert addButtonWithTitle:@"Done"];
    [alert addButtonWithTitle:@"Reset “Don’t show again” choices"];
    [alert addButtonWithTitle:@"Experimental partition detection…"];
    NSInteger answer = [alert runModal];
    if (answer == NSAlertThirdButtonReturn) { [self detectionSettings:sender]; return; }
    if (answer == NSAlertSecondButtonReturn) {
        NSUserDefaults *prefs = [NSUserDefaults standardUserDefaults];
        for (NSString *key in [NSArray arrayWithObjects:BackupWarningPreference,
             SecurityChipWarningPreference, LayoutPreference, @"InstallationGuidanceSuppressed-v2", nil])
            [prefs removeObjectForKey:key];
        [prefs synchronize];
        NSAlert *done = [[[NSAlert alloc] init] autorelease];
        [done setMessageText:@"Message choices reset"];
        [done setInformativeText:@"Messages you previously chose not to display will appear again when relevant. Launch messages will return the next time you open GoAwayBCA."];
        [done addButtonWithTitle:@"Done"];
        [done runModal];
    }
}

- (void)detectionSettings:(id)sender {

    if (busy) return;

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    [alert setMessageText:@"Experimental partition detection"];

    [alert setInformativeText:@"Normally, GoAwayBCA tries the method preferred for this macOS version first, then the other method if no eligible partitions are found. Reverse the order to investigate detection that appears successful but behaves incorrectly. Both methods retain the same destination protections. The selected order and results are saved in the diagnostic log."];

    NSButton *choice = [[[NSButton alloc] initWithFrame:NSMakeRect(0,0,440,28)] autorelease];

    [choice setButtonType:NSSwitchButton]; [choice setTitle:@"Reverse partition detection order"];

    [choice setState:[[NSUserDefaults standardUserDefaults] boolForKey:@"GoAwayBCAReversePartitionDetection"] ? NSOnState : NSOffState];

    NSView *settings=[[[NSView alloc] initWithFrame:NSMakeRect(0,0,460,62)] autorelease];
    [choice setFrameOrigin:NSMakePoint(0,30)]; [settings addSubview:choice];
    NSButton *ask=[[[NSButton alloc] initWithFrame:NSMakeRect(0,0,460,28)] autorelease];
    [ask setButtonType:NSSwitchButton]; [ask setTitle:@"Ask disk-layout questions again at next launch"];
    [ask setState:[[NSUserDefaults standardUserDefaults] dictionaryForKey:LayoutPreference] ? NSOffState : NSOnState];
    [settings addSubview:ask];
    [alert setAccessoryView:settings]; [alert addButtonWithTitle:@"Done"];

    [alert runModal];

    [[NSUserDefaults standardUserDefaults] setBool:[choice state]==NSOnState forKey:@"GoAwayBCAReversePartitionDetection"];
    if ([ask state]==NSOnState) [[NSUserDefaults standardUserDefaults] removeObjectForKey:LayoutPreference];

}

- (void)sendConfiguration:(id)unused {

    Send(peer,[NSDictionary dictionaryWithObjectsAndKeys:@"configuration",@"type",[isoField stringValue],@"iso",

        @"",@"prepared_media",workerOperation ?: @"install",@"operation",

        [NSNumber numberWithBool:[[NSUserDefaults standardUserDefaults] boolForKey:@"GoAwayBCAReversePartitionDetection"]],@"reverse_partition_detection",nil]);

}

- (void)authorizeWorker:(NSDictionary *)launch {

    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init];

    NSString *socketPath = [launch objectForKey:@"socket"], *ownedSession = [launch objectForKey:@"session"];

    NSString *ownedToken = [launch objectForKey:@"token"];

    NSDictionary *ownedProfile = [launch objectForKey:@"profile"];

    int authorizationWriter = [[launch objectForKey:@"authorization_writer"] intValue];

    char authorizationOutcome = 'S';

    NSMutableArray *arguments = [NSMutableArray array];

    NSArray *workerArguments = [NSArray arrayWithObjects:[Resources stringByAppendingPathComponent:[ownedProfile objectForKey:@"python"]],

         @"-I", @"-B", [Resources stringByAppendingPathComponent:[ownedProfile objectForKey:@"worker"]],

         @"--socket",socketPath,@"--token",ownedToken,@"--profile",[ownedProfile objectForKey:@"id"],

         @"--operation",[launch objectForKey:@"operation"],@"--session",nil];

    for (NSString *part in workerArguments) [arguments addObject:ShellQuote(part)];

    NSString *command = [NSString stringWithFormat:@"%@ > %@ 2>&1",[arguments componentsJoinedByString:@" "],

        ShellQuote([ownedSession stringByAppendingPathComponent:@"worker.log"])];

    NSString *script = [NSString stringWithFormat:@"do shell script %@ with administrator privileges",AppleScriptQuote(command)];

    NSTask *task = [[[NSTask alloc] init] autorelease];

    @try {

        /* Always use normal macOS administrator authorization, including on

         * development machines with an unrelated passwordless sudo grant. */

        [self performSelectorOnMainThread:@selector(appendLog:)

            withObject:@"Requesting macOS administrator authorization..." waitUntilDone:YES];

        [self performSelectorOnMainThread:@selector(updateProgress:)

            withObject:@{@"phase":@"Waiting for administrator authorization",@"determinate":@NO} waitUntilDone:YES];

        [task setLaunchPath:@"/usr/bin/osascript"];

        [task setArguments:[NSArray arrayWithObjects:@"-e",script,nil]];

        NSPipe *errors = [NSPipe pipe]; [task setStandardError:errors]; [task setStandardOutput:[NSPipe pipe]];

        [task launch]; NSData *error = [[errors fileHandleForReading] readDataToEndOfFile]; [task waitUntilExit];

        if ([task terminationStatus] != 0) {

            NSString *message = [[[NSString alloc] initWithData:error encoding:NSUTF8StringEncoding] autorelease];

            authorizationOutcome = message && [message rangeOfString:@"(-128)"].location!=NSNotFound ? 'C' : 'F';

            if (authorizationOutcome!='C')

                [self performSelectorOnMainThread:@selector(appendLog:)

                    withObject:message ? message : @"Administrator request failed. See worker.log." waitUntilDone:YES];

        }

    } @catch(NSException *error) {

        [self performSelectorOnMainThread:@selector(appendLog:) withObject:[error reason] waitUntilDone:YES];

        authorizationOutcome = 'F';

    }

    NMBFinishAuthorizationChannel(authorizationWriter,authorizationOutcome);

    [pool drain];

}

- (void)handleMessage:(NSDictionary *)message {

    NSString *kind = [message objectForKey:@"type"], *text = [message objectForKey:@"text"];

    if ([kind isEqual:@"verification_state"]) {

        NSString *phaseToken=[message objectForKey:@"phase_token"];

        if (![phaseToken isKindOfClass:[NSString class]] || [phaseToken length]!=32) return;

        if ([[message objectForKey:@"enabled"] boolValue]) {

            [verificationToken release]; verificationToken=[phaseToken copy];

            [skipVerificationButton setEnabled:busy];

        } else if ([verificationToken isEqual:phaseToken]) {

            [verificationToken release]; verificationToken=nil;

            [skipVerificationButton setEnabled:NO];

        }

        return;

    }

    if ([kind isEqual:@"log"]) {

        if ([text isKindOfClass:[NSString class]]) [self appendLog:text];

        id progress = [message objectForKey:@"progress"];

        if ([progress isKindOfClass:[NSDictionary class]]) [self updateProgress:progress];

        return;

    }

    if ([kind isEqual:@"done"]) { [self finish:text ? text : @"Worker finished. Inspect saved logs."]; return; }

    if ([kind isEqual:@"sip_choice"]) {
        NSAlert *alert=SIPChoiceAlert(text);
        NSString *action=SIPChoiceAction([alert runModal]);
        if (!DemoMode) Send(peer,@{@"type":@"reply",@"sequence":[message objectForKey:@"sequence"],@"value":action});
        return;
    }
    if ([kind isEqual:@"layout_declaration"]) {
        NSDictionary *reply=LayoutDeclaration(message);
        if (!reply) { [self setBusy:NO]; [NSApp terminate:nil]; return; }
        if (!DemoMode) Send(peer,@{@"type":@"reply",@"sequence":[message objectForKey:@"sequence"],@"value":reply});
        return;
    }

    if ([kind isEqual:@"firmware_block"]) {

        NSAlert *alert=FirmwareBlockAlert(message);

        NSString *action;

        do {

            action=FirmwareDialogAction(message,[alert runModal]);

            if ([action isEqual:@"guide"]) {

                NSPopUpButton *guides=(NSPopUpButton *)[[alert accessoryView] viewWithTag:601];

                NSString *url=[[guides selectedItem] representedObject];

                if (FirmwareGuideURLAllowed(url) && !SelfTest && !DemoMode)

                    [[NSWorkspace sharedWorkspace] openURL:[NSURL URLWithString:url]];

            }

        } while ([action isEqual:@"guide"]);

        if (!DemoMode) Send(peer,@{@"type":@"reply",@"sequence":[message objectForKey:@"sequence"],

            @"cancelled":@([action isEqual:@"cancel"]),@"value":action});

        return;

    }

    if ([kind isEqual:@"open_folder"]) {

        NSString *path = [message objectForKey:@"path"];

        BOOL valid = [path isKindOfClass:[NSString class]] && [path isAbsolutePath];

        NSInteger response=NSRunAlertPanel(@"GoAwayBCA",@"%@\n\n%@",@"Open in Finder",@"Done",nil,[message objectForKey:@"text"],valid ? path : @"Folder unavailable");

        if (valid && response==NSAlertDefaultReturn && !SelfTest && !DemoMode)

            [[NSWorkspace sharedWorkspace] openURL:[NSURL fileURLWithPath:path]];

        if (!DemoMode) Send(peer,@{@"type":@"reply",@"sequence":[message objectForKey:@"sequence"],@"value":@YES});

        return;

    }

    if ([kind isEqual:@"driver_storage"] || [kind isEqual:@"driver_mismatch"]) {

        NSAlert *alert=DriverChoiceAlert(kind,text ?: @"Choose a driver action.");

        NSInteger response;

        do {

            response=[alert runModal];

            if ([kind isEqual:@"driver_storage"] && response==NSAlertThirdButtonReturn) [self showSavedDrivers:nil];

        } while ([kind isEqual:@"driver_storage"] && response==NSAlertThirdButtonReturn);

        NSInteger value=response-NSAlertFirstButtonReturn;

        BOOL cancelled=value<0 || value>3 || ([kind isEqual:@"driver_storage"] && value>1);

        NSDictionary *reply=@{@"type":@"reply",@"sequence":[message objectForKey:@"sequence"],

            @"cancelled":@(cancelled),@"value":@(value)};

        if (!DemoMode) Send(peer,reply);

        return;

    }

    if ([kind isEqual:@"driver_save_folder"]) {

        NSOpenPanel *panel=[NSOpenPanel openPanel];

        [panel setTitle:@"Save drivers in a custom location (manual use only)"];

        [panel setCanChooseDirectories:YES]; [panel setCanChooseFiles:NO]; [panel setAllowsMultipleSelection:NO];

        [panel setCanCreateDirectories:YES];

        BOOL selected=[panel runModalForDirectory:nil file:nil types:nil]==NSOKButton;

        NSDictionary *reply=[NSDictionary dictionaryWithObjectsAndKeys:@"reply",@"type",[message objectForKey:@"sequence"],@"sequence",

            [NSNumber numberWithBool:!selected],@"cancelled",selected ? [[panel filenames] objectAtIndex:0] : @"",@"value",nil];

        if (!DemoMode) Send(peer,reply);

        return;

    }

    NSAlert *alert = [self alertForMessage:message];

    if (!alert) return;

    NSView *accessory = [alert accessoryView];

    int response;

    NSString *details=[message objectForKey:@"details"];

    do {

        response=(int)[alert runModal];

        if ([details isKindOfClass:[NSString class]] && [details length]

            && response==NSAlertThirdButtonReturn) [DetailsAlert(details) runModal];

    } while ([details isKindOfClass:[NSString class]] && [details length]

             && response==NSAlertThirdButtonReturn);

    NSMutableDictionary *reply = [NSMutableDictionary dictionaryWithObjectsAndKeys:@"reply",@"type",

        [message objectForKey:@"sequence"],@"sequence",[NSNumber numberWithBool:response != NSAlertFirstButtonReturn],@"cancelled",nil];

    if (response == NSAlertFirstButtonReturn) {

        id value;

        if ([kind isEqual:@"saved_files_selection"]) {

            NSMutableArray *selected=[NSMutableArray array];

            for (NSView *item in [[[accessory viewWithTag:800] superview] subviews])

                if ([item isKindOfClass:[NSButton class]] && [(NSButton *)item state]==NSOnState)

                    [selected addObject:@([item tag]-800)];

            value=selected;

        } else if ([kind isEqual:@"choose"])

            value = [NSNumber numberWithInt:(int)[(NSPopUpButton *)[accessory viewWithTag:201] indexOfSelectedItem] - 1];

        else if ([kind isEqual:@"confirm"])

            value = [(NSTextField *)[accessory viewWithTag:101] stringValue];

        else if ([kind isEqual:@"driver_model"])

            value = [(NSTextField *)[accessory viewWithTag:401] stringValue];

        else if ([kind isEqual:@"entry_name"])

            value = [(NSTextField *)[accessory viewWithTag:501] stringValue];

        else if ([kind isEqual:@"diagnostic_notes"])

            value = [EditableText(accessory) string];

        else if ([kind isEqual:@"efi_choice"] || [kind isEqual:@"keyboard_choice"] || [kind isEqual:@"nvme_choice"])

            value = [NSNumber numberWithBool:[(NSButton *)[accessory viewWithTag:301] state] == NSOnState];

        else value = [NSNumber numberWithBool:YES];

        [reply setObject:value forKey:@"value"];

    }

    if (DemoMode) [self appendLog:[NSString stringWithFormat:@"Simulated response: %@",reply]];

    else Send(peer,reply);

    choiceContinue = nil;

}

- (NSAlert *)alertForMessage:(NSDictionary *)message {

    NSString *kind = [message objectForKey:@"type"], *text = [message objectForKey:@"text"];

    if (![kind isEqual:@"choose"] && ![kind isEqual:@"confirm"]

        && ![kind isEqual:@"format_ready"] && ![kind isEqual:@"review"]

        && ![kind isEqual:@"efi_choice"] && ![kind isEqual:@"keyboard_choice"] && ![kind isEqual:@"nvme_choice"] && ![kind isEqual:@"driver_model"]

        && ![kind isEqual:@"entry_name"] && ![kind isEqual:@"rename_review"]

        && ![kind isEqual:@"rebuild_review"] && ![kind isEqual:@"reset_review"] && ![kind isEqual:@"diagnostic_notes"]

        && ![kind isEqual:@"saved_files_selection"] && ![kind isEqual:@"saved_files_confirm"]

        && ![kind isEqual:@"compatibility_review"] && ![kind isEqual:@"import_review"]
        && ![kind isEqual:@"notice"]) return nil;

    NSAlert *alert = [[[NSAlert alloc] init] autorelease];

    [alert setMessageText:DemoMode ? @"UI simulation" : @"GoAwayBCA"];

    [alert setInformativeText:text ? text : @"Selection required"];

    NSString *action = @"Continue";

    if ([kind isEqual:@"notice"]) action = [[message objectForKey:@"button_title"] isEqual:@"Done"] ? @"Done" : @"OK";

    if ([kind isEqual:@"format_ready"]) action = [workerOperation isEqual:@"manage"] ? @"Format selected partition…" : @"Format selected partition and install…";

    else if ([kind isEqual:@"review"]) action = @"I checked this partition — continue";

    else if ([kind isEqual:@"confirm"]) action = [workerOperation isEqual:@"reset-menu"] ? @"Delete Windows boot menu" : ([workerOperation isEqual:@"manage"] ? @"Erase selected partition" : @"Erase selected partition and install");

    else if ([kind isEqual:@"rename_review"]) action = @"Rename picker entry";

    else if ([kind isEqual:@"rebuild_review"]) action = @"Rebuild boot menu";

    else if ([kind isEqual:@"reset_review"]) action = @"Continue";

    else if ([kind isEqual:@"saved_files_selection"]) action = @"Review selected files…";

    else if ([kind isEqual:@"saved_files_confirm"]) action = @"Delete selected files";

    [alert addButtonWithTitle:action];

    if (![kind isEqual:@"notice"]) [alert addButtonWithTitle:@"Cancel"];

    NSString *details=[message objectForKey:@"details"];

    if ([details isKindOfClass:[NSString class]] && [details length]) [alert addButtonWithTitle:@"Details…"];

    NSView *accessory;

    if ([kind isEqual:@"saved_files_selection"]) {

        NSArray *items=[message objectForKey:@"items"];

        if (![items isKindOfClass:[NSArray class]]) return nil;

        NSScrollView *scroll=[[[NSScrollView alloc] initWithFrame:NSMakeRect(0,0,550,200)] autorelease];

        [scroll setHasVerticalScroller:YES]; [scroll setBorderType:NSBezelBorder];

        CGFloat height=MAX(200,[items count]*66);

        NSView *list=[[[NSView alloc] initWithFrame:NSMakeRect(0,0,530,height)] autorelease];

        NSUInteger index=0;

        for (NSDictionary *item in items) {

            CGFloat y=height-(++index)*66;

            NSButton *check=[[[NSButton alloc] initWithFrame:NSMakeRect(6,y+34,518,28)] autorelease];

            [check setButtonType:NSSwitchButton]; [check setState:NSOffState];

            [check setTitle:[item objectForKey:@"title"] ?: @"Saved item"];

            [check setFont:[NSFont systemFontOfSize:11]];

            [check setTag:800+[[item objectForKey:@"id"] integerValue]];

            [check setTarget:self]; [check setAction:@selector(savedFilesChanged:)]; [list addSubview:check];

            NSTextField *detail=Label(list,[item objectForKey:@"detail"] ?: @"",NSMakeRect(28,y+2,496,32),NO);

            [detail setFont:[NSFont systemFontOfSize:10]]; [[detail cell] setWraps:YES]; [[detail cell] setScrollable:NO];

            [check setToolTip:[item objectForKey:@"detail"]];

        }

        [scroll setDocumentView:list]; [list scrollPoint:NSMakePoint(0,height-200)];

        accessory=scroll; choiceContinue=[[alert buttons] objectAtIndex:0]; [choiceContinue setEnabled:NO];

    } else if ([kind isEqual:@"choose"]) {

        NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,160)] autorelease];

        NSPopUpButton *choices = [[[NSPopUpButton alloc] initWithFrame:NSMakeRect(0,128,620,28) pullsDown:NO] autorelease];

        NSArray *options = [message objectForKey:@"options"];

        if (![options isKindOfClass:[NSArray class]] || ![options count]) return nil;

        [choices addItemWithTitle:@"Choose an option…"];

        [choices addItemsWithTitles:options]; [choices setTag:201];

        [choices setTarget:self]; [choices setAction:@selector(selectionChanged:)];

        [box addSubview:choices];

        NSTextField *detail = Label(box,@"Select an item to review its complete details.",NSMakeRect(0,0,620,120),NO);

        [detail setTag:202]; [[detail cell] setWraps:YES]; [[detail cell] setScrollable:NO];

        choiceContinue = [[alert buttons] objectAtIndex:0]; [choiceContinue setEnabled:NO];

        accessory=box;

    } else if ([kind isEqual:@"diagnostic_notes"]) {

        NSScrollView *box = [[[NSScrollView alloc] initWithFrame:NSMakeRect(0,0,550,130)] autorelease];

        [box setHasVerticalScroller:YES]; [box setBorderType:NSBezelBorder];

        NSTextView *entry = [[[NSTextView alloc] initWithFrame:NSMakeRect(0,0,530,130)] autorelease];

        [entry setRichText:NO]; [entry setEditable:YES];

        [entry setToolTip:@"Optional symptom notes, up to 4000 characters. Do not include secrets or personal information."];

        [box setDocumentView:entry]; accessory=box;

    } else if ([kind isEqual:@"entry_name"]) {

        NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,80)] autorelease];

        Label(box,@"Windows picker name",NSMakeRect(0,48,620,24),YES);

        NSTextField *entry = [[[NSTextField alloc] initWithFrame:NSMakeRect(0,8,610,30)] autorelease];

        [entry setTag:501]; [entry setStringValue:[message objectForKey:@"name"] ?: @""];

        [box addSubview:entry]; accessory=box;

    } else if ([kind isEqual:@"driver_model"]) {

        NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,80)] autorelease];

        Label(box,@"Confirm your Mac model identifier",NSMakeRect(0,48,620,24),YES);

        NSTextField *entry = [[[NSTextField alloc] initWithFrame:NSMakeRect(0,8,610,30)] autorelease];

        [entry setTag:401]; [entry setStringValue:[message objectForKey:@"model"] ?: @""];

        [box addSubview:entry]; accessory=box;

        [[[alert buttons] objectAtIndex:0] setTitle:@"Confirm this model"];

    } else if ([kind isEqual:@"efi_choice"] || [kind isEqual:@"keyboard_choice"] || [kind isEqual:@"nvme_choice"]) {

        NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,44)] autorelease];

        NSButton *choice = [[[NSButton alloc] initWithFrame:NSMakeRect(0,8,610,28)] autorelease];

        [choice setButtonType:NSSwitchButton];

        BOOL enabled = ![message objectForKey:@"default_enabled"] || [[message objectForKey:@"default_enabled"] boolValue];

        [choice setTitle:[kind isEqual:@"nvme_choice"] ? @"Try the optional NVMe driver (recommended; experimental)" : [kind isEqual:@"keyboard_choice"] ? @"Prepare Windows 7 keyboard drivers (recommended)" :
            (enabled ? @"Experimental EFI fixes (recommended)" : @"Use experimental EFI fixes (not recommended unless needed)")];

        [choice setState:enabled ? NSOnState : NSOffState]; [choice setTag:301]; [box addSubview:choice]; accessory=box;

    } else if ([kind isEqual:@"confirm"]) {

        NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,80)] autorelease];

        NSTextField *prompt = Label(box,[NSString stringWithFormat:@"Type %@ to continue (not case-sensitive).",[message objectForKey:@"phrase"]],NSMakeRect(0,48,620,24),YES);

        [prompt setTag:102];

        NSTextField *entry = [[[NSTextField alloc] initWithFrame:NSMakeRect(0,8,610,30)] autorelease];

        [entry setTag:101]; [entry setStringValue:@""]; [box addSubview:entry]; accessory=box;

    } else {

        accessory = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,620,1)] autorelease];

    }

    if (![kind isEqual:@"choose"] && ![kind isEqual:@"notice"]) {

        [[[alert buttons] objectAtIndex:0] setKeyEquivalent:@""];

        [[[alert buttons] objectAtIndex:1] setKeyEquivalent:@"\r"];

    }

    [alert setAccessoryView:accessory];

    BoundAlertText(alert,DialogSpace(),[kind isEqual:@"format_ready"] || [kind isEqual:@"review"] || [kind isEqual:@"confirm"] || [kind isEqual:@"saved_files_confirm"]);

    return alert;

}

- (void)savedFilesChanged:(NSButton *)sender {

    BOOL selected=NO;

    for (NSView *item in [[sender superview] subviews])

        if ([item isKindOfClass:[NSButton class]] && [(NSButton *)item state]==NSOnState) selected=YES;

    [choiceContinue setEnabled:selected];

}

- (void)selectionChanged:(NSPopUpButton *)sender {

    BOOL selected = [sender indexOfSelectedItem] > 0;

    [choiceContinue setEnabled:selected];

    [(NSTextField *)[[sender superview] viewWithTag:202] setStringValue:

        selected ? [sender titleOfSelectedItem] : @"Select an item to review its complete details."];

}

- (int)selfTest {

    /* Stable offscreen QA appearance only; normal launches inherit the OS.

     * No user preference is written and old hosts need no NSAppearance API. */

    Class qaAppearanceClass=NSClassFromString(@"NSAppearance");

    if (qaAppearanceClass && [NSApp respondsToSelector:@selector(setAppearance:)]) {

        id aqua=[qaAppearanceClass performSelector:@selector(appearanceNamed:) withObject:@"NSAppearanceNameAqua"];

        if (aqua) {

            [NSApp performSelector:@selector(setAppearance:) withObject:aqua];

            [qaAppearanceClass performSelector:@selector(setCurrentAppearance:) withObject:aqua];

        }

    }

    [self buildWindow];

    /* Construct dialogs without showing them or touching user preferences. */

    NSString *dialogOutput = [[[NSProcessInfo processInfo] environment] objectForKey:@"DEPLOYER_UI_PREVIEW_PNG"];

    TestDialogSpace=NSMakeSize(800,560);

    NSAlert *sipPreview=SIPChoiceAlert(@"SIP could not be conclusively identified. Keep SIP enabled to confirm the layout yourself, view instructions, or try the automatic read-only check using your current settings. The app does not change SIP and stops if it cannot check the disk or finds an incompatible layout.");
    if ([[sipPreview buttons] count]!=3 || ![[[[sipPreview buttons] objectAtIndex:2] title] isEqual:@"Continue as is / SIP is disabled"]) return 91;
    if (![SIPChoiceAction(NSAlertFirstButtonReturn) isEqual:@"guide"] || ![SIPChoiceAction(NSAlertSecondButtonReturn) isEqual:@"manual"] || ![SIPChoiceAction(NSAlertThirdButtonReturn) isEqual:@"automatic"] || ![SIPChoiceAction(0) isEqual:@"cancel"]) return 92;
    if (dialogOutput) { [sipPreview layout]; SaveViewPreview([[sipPreview window] contentView],[dialogOutput stringByAppendingString:@".sip-choice.png"]); }
    NSAlert *layoutQuestion=LayoutAlert(@"question",@"Fixture: no disk access");
    if ([[layoutQuestion buttons] count]!=3 || ![[[[layoutQuestion buttons] objectAtIndex:2] title] isEqual:@"I don't know"]) return 80;
    NSAlert *layoutFinal=LayoutAlert(@"final",@"Fixture: final confirmation");
    if ([[layoutFinal buttons] count]!=2 || ![layoutFinal showsSuppressionButton] || [[layoutFinal suppressionButton] state]!=NSOffState) return 81;
    NSAlert *layoutBlocked=LayoutAlert(@"blocked",@"Fixture: no disk access");
    if ([[layoutBlocked buttons] count]!=1 || ![[[[layoutBlocked buttons] objectAtIndex:0] title] isEqual:@"Quit"]) return 82;
    NSAlert *layoutVersions=LayoutAlert(@"versions",@"Fixture: installed versions");
    for (NSInteger tag=710;tag<=716;tag++) {
        NSButton *b=(NSButton *)[[layoutVersions accessoryView] viewWithTag:tag];
        if (!b || [b state]!=NSOffState) return 83;
    }
    NSAlert *saved=[self alertForMessage:@{@"type":@"saved_files_selection",@"text":@"Select saved files to delete. Nothing is selected automatically.",

        @"items":@[@{@"id":@0,@"title":@"ISO preparation (4.20 GB)",@"detail":@"Previous attempt; original ISO and logs are kept"},

                   @{@"id":@1,@"title":@"Saved drivers — MacBookPro11,5 / Windows 10 (2.78 GB)",@"detail":@"Deleting this requires downloading the drivers again"}]}];

    NSButton *savedCheck=(NSButton *)[[saved accessoryView] viewWithTag:800];

    NSScrollView *savedList=[[savedCheck superview] enclosingScrollView];

    if (!savedList || !savedCheck || [savedCheck state]!=NSOffState || [choiceContinue isEnabled]) return 51;

    [savedCheck setState:NSOnState];[self savedFilesChanged:savedCheck];

    if (![choiceContinue isEnabled]) return 52;

    [savedCheck setState:NSOffState];[self savedFilesChanged:savedCheck];

    if ([choiceContinue isEnabled] || ![[[[saved buttons] objectAtIndex:1] keyEquivalent] isEqual:@"\r"]) return 53;

    if (dialogOutput) {[saved layout];SaveViewPreview([[saved window] contentView],[dialogOutput stringByAppendingString:@".saved-files.png"]);}

    NSAlert *savedConfirm=[self alertForMessage:@{@"type":@"saved_files_confirm",@"text":@"Delete the selected saved files?\n\nThis permanently deletes only your selection. Installation records, logs and EFI backups are kept."}];

    [savedConfirm layout];

    if (NSHeight([[savedConfirm window] frame])>560 || ![[[[savedConfirm buttons] objectAtIndex:0] title] isEqual:@"Delete selected files"]

        || ![[[[savedConfirm buttons] objectAtIndex:1] keyEquivalent] isEqual:@"\r"]) return 54;

    if (dialogOutput) SaveViewPreview([[savedConfirm window] contentView],[dialogOutput stringByAppendingString:@".saved-files-confirm.png"]);

    TestDialogSpace=NSZeroSize;

    NSDictionary *firmwareMessage=@{@"code":@"firmware-image-mismatch",@"text":@"This Mac reports 32-bit EFI; the selected Windows image is 64-bit. Nothing will be formatted.",

        @"links":@[@{@"title":@"EFI versus BIOS boot mode",@"url":@"https://www.rodsbooks.com/refind/bootmode.html",@"scope":@"External documentation; no disk changes."},

                  @{@"title":@"Untrusted",@"url":@"file:///tmp/execute"}]};

    NSAlert *firmwareAlert=FirmwareBlockAlert(firmwareMessage);

    if ([FirmwareGuideLinks(firmwareMessage) count]!=1 || [[firmwareAlert buttons] count]!=3

        || FirmwareGuideURLAllowed(@"https://www.rodsbooks.com/refind/bootmode.html?run=anything")

        || ![FirmwareDialogAction(firmwareMessage,NSAlertFirstButtonReturn) isEqual:@"guide"]

        || ![FirmwareDialogAction(firmwareMessage,NSAlertSecondButtonReturn) isEqual:@"choose-image"]

        || ![FirmwareDialogAction(firmwareMessage,NSAlertThirdButtonReturn) isEqual:@"cancel"]

        || ![FirmwareDialogAction(firmwareMessage,9999) isEqual:@"cancel"]) return 31;

    NSDictionary *unknownFirmware=@{@"code":@"firmware-architecture-unknown",@"text":@"EFI architecture could not be read. Retry detection or cancel.",@"links":@[]};

    NSAlert *unknownAlert=FirmwareBlockAlert(unknownFirmware);

    if ([[unknownAlert buttons] count]!=2

        || ![FirmwareDialogAction(unknownFirmware,NSAlertFirstButtonReturn) isEqual:@"retry"]

        || ![FirmwareDialogAction(unknownFirmware,NSAlertSecondButtonReturn) isEqual:@"cancel"]

        || ![[[[unknownAlert buttons] lastObject] keyEquivalent] isEqual:@"\r"]) return 32;

    if (dialogOutput) {

        [firmwareAlert layout]; SaveViewPreview([[firmwareAlert window] contentView],[dialogOutput stringByAppendingString:@".firmware.png"]);

        [unknownAlert layout]; SaveViewPreview([[unknownAlert window] contentView],[dialogOutput stringByAppendingString:@".firmware-unknown.png"]);

    }

    NSAlert *backup = BackupWarning();

    if (![[[backup suppressionButton] title] isEqual:@"Do not show this message again"]

        || [[backup suppressionButton] state] != NSOffState

        || RememberBackupWarningChoice(NSAlertFirstButtonReturn, NSOffState)

        || RememberBackupWarningChoice(NSAlertSecondButtonReturn, NSOnState)

        || !RememberBackupWarningChoice(NSAlertFirstButtonReturn, NSOnState)) return 10;

    if (dialogOutput) {

        [backup layout];

        SaveViewPreview([[backup window] contentView], [dialogOutput stringByAppendingString:@".backup.png"]);

        NSAlert *machine = [self machineAlert]; [machine layout];

        SaveViewPreview([[machine window] contentView], [dialogOutput stringByAppendingString:@".machine.png"]);

    }

    for (NSString *model in [NSArray arrayWithObjects:@"MacBookPro13,2",@"MacBookPro13,3",

            @"MacBookPro14,2",@"MacBookPro14,3",@"MacBookPro15,1",@"MacBookPro15,2",

            @"MacBookPro15,3",@"MacBookPro15,4",@"MacBookPro16,1",@"MacBookPro16,2",

            @"MacBookPro16,3",@"MacBookPro16,4",@"MacBookAir8,1",@"MacBookAir8,2",

            @"MacBookAir9,1",@"Macmini8,1",@"iMacPro1,1",@"iMac20,1",@"iMac20,2",@"MacPro7,1",nil]) {

        NSAlert *chip = SecurityChipWarning(model);

        if (!chip || !ShouldShowSecurityChipWarning(model,NO)

            || ShouldShowSecurityChipWarning(model,YES)

            || [[chip suppressionButton] state] != NSOffState

            || ![[[chip suppressionButton] title] isEqual:@"Don't show me this message again"]

            || [[chip buttons] count] != 2

            || ![[[[chip buttons] objectAtIndex:0] title] isEqual:@"Continue"]

            || ![[[[chip buttons] objectAtIndex:1] title] isEqual:@"Quit"]) return 18;

    }

    for (NSString *other in [NSArray arrayWithObjects:@"MacBookPro11,5",@"MacBookPro13,1",

            @"MacBookPro14,1",@"MacBookPro17,1",@"iMac19,1",@"Unknown model",@"",nil])

        if (SecurityChipWarning(other) || ShouldShowSecurityChipWarning(other,NO)) return 19;

    if (![[SecurityChipForModel(@"MacBookPro13,2") description] isEqual:@"T1"]

        || ![[SecurityChipForModel(@"MacBookPro15,1") description] isEqual:@"T2"]

        || [SecurityChipGuidance(@"MacBookPro15,1") rangeOfString:@"Please do not submit GitHub issues or diagnostics"].location==NSNotFound

        || [SecurityChipGuidance(@"MacBookPro13,2") rangeOfString:@"you may submit a GitHub issue"].location==NSNotFound

        || RememberSecurityChipWarningChoice(NSAlertFirstButtonReturn,NSOffState)

        || RememberSecurityChipWarningChoice(NSAlertSecondButtonReturn,NSOnState)

        || !RememberSecurityChipWarningChoice(NSAlertFirstButtonReturn,NSOnState)) return 20;

    if (dialogOutput) for (NSString *model in [NSArray arrayWithObjects:@"MacBookPro13,2",@"MacBookPro15,1",nil]) {

        NSAlert *chip = SecurityChipWarning(model); [chip layout];

        SaveViewPreview([[chip window] contentView], [dialogOutput stringByAppendingFormat:@".%@.png",SecurityChipForModel(model)]);

    }

    if ([ModelGuidance(@"MacBookPro11,5") rangeOfString:@"tested on MacBookPro11,5"].location == NSNotFound

        || [ModelGuidance(@"MacBookPro11,4") rangeOfString:@"tested on MacBookPro11,4"].location == NSNotFound

        || [ModelGuidance(@"MacBookPro11,4") rangeOfString:@"4096-byte logical sectors"].location == NSNotFound

        || ![HardwareModelGuidance(@"MacBookPro15,1") isEqual:@"Apple T2 model detected."]

        || ![HardwareModelGuidance(@"MacBookPro13,2") isEqual:@"Apple T1 model detected."]

        || [ModelGuidance(@"MacBookPro999,1") rangeOfString:@"You may try this Intel Mac"].location == NSNotFound

        || [ModelGuidance(@"MacBookPro999,1") rangeOfString:@"Pure EFI only"].location == NSNotFound

        || ![HardwareModel() length]) return 13;

    NSAlert *efiChoice = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

        @"efi_choice",@"type",@"MacBookPro11,4 + Windows 7 (fixture only)",@"text",nil]];

    NSButton *efiCheck = (NSButton *)[[efiChoice accessoryView] viewWithTag:301];

    if (!efiCheck || [efiCheck state] != NSOnState) return 15;

    [efiCheck setState:NSOffState];

    if ([efiCheck state] != NSOffState) return 16;

    NSAlert *stockEFI = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

        @"efi_choice",@"type",@"Stock baseline fixture",@"text",@NO,@"default_enabled",nil]];

    if ([(NSButton *)[[stockEFI accessoryView] viewWithTag:301] state] != NSOffState) return 23;

    NSAlert *keyboardChoice = [self alertForMessage:@{@"type":@"keyboard_choice", @"text":@"Windows 7 keyboard preparation", @"default_enabled":@YES}];
    NSButton *keyboardCheck=(NSButton *)[[keyboardChoice accessoryView] viewWithTag:301];
    if (!keyboardCheck || [keyboardCheck state]!=NSOnState || [[keyboardCheck title] rangeOfString:@"keyboard"].location==NSNotFound) return 29;
    NSAlert *nvmeChoice = [self alertForMessage:@{@"type":@"nvme_choice", @"text":@"Optional Windows 8 NVMe compatibility", @"default_enabled":@YES}];
    NSButton *nvmeCheck=(NSButton *)[[nvmeChoice accessoryView] viewWithTag:301];
    if (!nvmeCheck || [nvmeCheck state]!=NSOnState || [[nvmeCheck title] rangeOfString:@"experimental"].location==NSNotFound) return 29;
    NSAlert *driverModel = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

        @"driver_model",@"type",@"Confirm model (fixture)",@"text",@"MacBookPro11,5",@"model",nil]];

    NSTextField *modelField = (NSTextField *)[[driverModel accessoryView] viewWithTag:401];

    if (![[modelField stringValue] isEqual:@"MacBookPro11,5"] || ![modelField isEditable]) return 24;

    [modelField setStringValue:@"MacBookPro11,4"];

    if (![[modelField stringValue] isEqual:@"MacBookPro11,4"]) return 25;

    NSAlert *entryName = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

        @"entry_name",@"type",@"Name this menu entry (fixture)",@"text",@"Windows 10",@"name",nil]];

    NSTextField *nameField = (NSTextField *)[[entryName accessoryView] viewWithTag:501];

    if (!nameField || ![nameField isEditable] || ![[nameField stringValue] isEqual:@"Windows 10"]) return 26;

    [nameField setStringValue:@"Windows 10 - Gaming"];

    NSAlert *notes = [self alertForMessage:@{@"type":@"diagnostic_notes",@"text":@"Optional symptom notes (fixture only)"}];

    NSTextView *notesField=(NSTextView *)[(NSScrollView *)[notes accessoryView] documentView];

    if (!notesField || ![notesField isEditable] || [notesField isRichText]) return 38;

    [notesField setString:@"Dock Ethernet fails. Wi-Fi works. Boot route unknown."];

    if (![[notesField string] hasPrefix:@"Dock Ethernet"]) return 39;

    NSAlert *rename = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

        @"rename_review",@"type",@"Rename the picker entry only; no formatting.",@"text",nil]];

    if (![[[[rename buttons] objectAtIndex:0] title] isEqual:@"Rename picker entry"]) return 27;

    if (dialogOutput) {

        [notes layout]; SaveViewPreview([[notes window] contentView],[dialogOutput stringByAppendingString:@".diagnostic-notes.png"]);

        [driverModel layout]; SaveViewPreview([[driverModel window] contentView],[dialogOutput stringByAppendingString:@".driver-model.png"]);

        [stockEFI layout]; SaveViewPreview([[stockEFI window] contentView],[dialogOutput stringByAppendingString:@".efi-stock.png"]);

        [entryName layout]; SaveViewPreview([[entryName window] contentView],[dialogOutput stringByAppendingString:@".entry-name.png"]);

        [rename layout]; SaveViewPreview([[rename window] contentView],[dialogOutput stringByAppendingString:@".rename.png"]);

    }

    TestDialogSpace=NSMakeSize(800,560);

    for (NSString *kind in [NSArray arrayWithObjects:@"format_ready",@"review",@"confirm",nil]) {

        NSString *fixtureText = [NSString stringWithFormat:@"%@ (invented fixture; no disk access)\n\nLabel: \"WIN7\"\nCapacity: 149.84 GB (139.54 GiB)\nFree space: 149.80 GB (139.51 GiB)\nFilesystem: EXFAT\nPartition: disk99s3\nDrive: \"Fixture internal SSD\"\nPartition UUID: 00000000-0000-0000-0000-000000000001\n\nDouble-check the label, capacity, free space and partition identifier. Selecting the wrong partition will cause data loss. ALL existing contents of the selected partition will be erased, including any Windows installation. Its contents will NOT be backed up by this tool. The tool and its authors are not responsible for data loss caused by selecting the wrong partition.", kind];

        NSAlert *check = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

            kind,@"type",fixtureText,@"text",@"confirm",@"phrase",@"Shared EFI files are retained. Full file list and manual cleanup guidance.",@"details",nil]];

        if (!check || [[check buttons] count] != 3

            || ![[[[check buttons] objectAtIndex:0] keyEquivalent] isEqual:@""]

            || ![[[[check buttons] objectAtIndex:1] keyEquivalent] isEqual:@"\r"]) return 11;

        if ([kind isEqual:@"confirm"] && ![[[(NSTextField *)[[check accessoryView] viewWithTag:101] stringValue] description] isEqual:@""]) return 12;

        if ([kind isEqual:@"confirm"] && ![[(NSTextField *)[[check accessoryView] viewWithTag:102] stringValue]

            isEqual:@"Type confirm to continue (not case-sensitive)."]) return 14;

        if (dialogOutput) {

            [check layout];

            SaveViewPreview([[check window] contentView], [dialogOutput stringByAppendingFormat:@".%@.png", kind]);

        }

        if ([[check window] frame].size.height>560 || [[check window] frame].size.width>800) {

            fprintf(stderr,"Oversized %s dialog: %.0f x %.0f\n",[kind UTF8String],[[check window] frame].size.width,[[check window] frame].size.height);

            return 60;

        }

    }

    NSAlert *withDetails=[self alertForMessage:@{@"type":@"review",@"text":@"Partition review",@"details":@"Saved file list and manual cleanup guidance"}];

    if ([[withDetails buttons] count]!=3 || ![[[[withDetails buttons] objectAtIndex:2] title] isEqual:@"Details…"]) return 61;

    NSAlert *emptyNotice=[self alertForMessage:@{@"type":@"notice",@"text":@"No Windows installations found. No files changed."}];

    NSAlert *doneNotice=[self alertForMessage:@{@"type":@"notice",@"text":@"Deployment complete (fixture).",@"button_title":@"Done"}];

    if ([[doneNotice buttons] count]!=1 || ![[[[doneNotice buttons] objectAtIndex:0] title] isEqual:@"Done"]

        || [[[doneNotice accessoryView] subviews] count]) return 63;

    if ([[emptyNotice buttons] count]!=1 || ![[[[emptyNotice buttons] objectAtIndex:0] title] isEqual:@"OK"]) return 62;

    NSAlert *detailPreview=DetailsAlert([@"Long saved EFI path and manual cleanup guidance.\n" stringByPaddingToLength:6000 withString:@"Long saved EFI path and manual cleanup guidance.\n" startingAtIndex:0]);

    if ([[detailPreview window] frame].size.height>560 || [[detailPreview window] frame].size.width>800) return 63;

    if (dialogOutput) SaveViewPreview([[detailPreview window] contentView],[dialogOutput stringByAppendingString:@".cleanup-details.png"]);

    TestDialogSpace=NSZeroSize;

    for (NSString *kind in @[@"compatibility_review",@"import_review"]) {
        NSAlert *check=[self alertForMessage:@{@"type":kind,@"text":@"Consent fixture"}];
        if (!check || [[check buttons] count]!=2 || ![[[check buttons] objectAtIndex:0] isEnabled]
            || ![[[[check buttons] objectAtIndex:0] title] isEqual:@"Continue"]
            || ![[[[check buttons] objectAtIndex:1] title] isEqual:@"Cancel"]) return 80;
    }

    workerOperation = @"manage";

    for (NSString *kind in [NSArray arrayWithObjects:@"format_ready",@"confirm",nil]) {

        NSAlert *check = [self alertForMessage:[NSDictionary dictionaryWithObjectsAndKeys:

            kind,@"type",@"Management fixture; no device access",@"text",@"confirm",@"phrase",nil]];

        NSString *expected = [kind isEqual:@"format_ready"] ? @"Format selected partition…" : @"Erase selected partition";

        if (![[[[check buttons] objectAtIndex:0] title] isEqual:expected]) return 21;

    }

    workerOperation = @"reset-menu";

    for (NSString *kind in [NSArray arrayWithObjects:@"reset_review",@"confirm",nil]) {

        NSAlert *check = [self alertForMessage:@{@"type":kind,@"text":@"Reset fixture; no device access",@"phrase":@"confirm"}];

        NSString *expected = [kind isEqual:@"reset_review"] ? @"Continue" : @"Delete Windows boot menu";

        if (!check || ![[[[check buttons] objectAtIndex:0] title] isEqual:expected]) return 42;

        if (![[[[check buttons] objectAtIndex:1] keyEquivalent] isEqual:@"\r"]) return 43;

    }

    [self debugReset:nil]; /* SelfTest must never launch a worker. */

    if (busy || !debugButton) return 44;
    if (![[debugButton title] isEqual:@"Delete Boot Menu…"]) return 81;

    workerOperation = @"install";

    if (CompareVersions(@"10.9",@"10.15") >= 0 || CompareVersions(@"26.7.1",@"10.15") <= 0) return 2;

    if (SelectProfile([configuration objectForKey:@"profiles"],@"10.4.6",@"i386")) return 3;

    for (NSString *supported in [NSArray arrayWithObjects:@"10.7.5",@"10.8.5",@"10.9.5",@"10.10.5",

            @"10.11.6",@"10.12.6",@"10.13.6",@"10.14.6",@"10.15.7",@"11.0",@"12.0",@"13.0",@"14.0",@"15.0",@"26.0",nil])

        if (!SelectProfile([configuration objectForKey:@"profiles"],supported,@"x86_64")) return 3;

    for (NSString *invalid in [NSArray arrayWithObjects:@"16.0",@"25.0",@"10.16",@"26.7.1.4",@"26.bad",nil])

        if (SelectProfile([configuration objectForKey:@"profiles"],invalid,@"x86_64")) return 3;

    [self handleMessage:@{@"type":@"log",@"text":@"Verifying driver files: 1228 of 2456 files verified (50.0%)",@"progress":@{@"phase":@"Verifying driver files",@"determinate":@YES,@"percent":@50}}];

    if ([activity isIndeterminate] || [activity doubleValue] != 50 || [[status stringValue] rangeOfString:@"50.0%"].location == NSNotFound) return 28;

    if (dialogOutput) SaveViewPreview([window contentView],[dialogOutput stringByAppendingString:@".progress.png"]);

    [self updateProgress:@{@"phase":@"Closing temporary mounts",@"determinate":@NO}];

    if (![activity isIndeterminate] || ![[status stringValue] isEqual:@"Closing temporary mounts"]) return 29;

    [self updateProgress:@{@"phase":@"New copy phase",@"determinate":@YES,@"percent":@0}];

    if ([activity isIndeterminate] || [activity doubleValue] != 0) return 30;

    [self updateProgress:@{@"phase":@"New copy phase",@"determinate":@YES,@"percent":@100}];

    if ([activity isIndeterminate] || [activity doubleValue] != 100) return 31;

    [self setBusy:YES];

    if (![activity isIndeterminate]) return 32;

    [self setBusy:NO];

    if (!profile || !VerifyProfile(profile)) return 4;

    int pair[2]; if (socketpair(AF_UNIX,SOCK_STREAM,0,pair)) return 5;

    NSDictionary *value = [NSDictionary dictionaryWithObjectsAndKeys:@"fixture",@"type",@"Unicode 雪 & quotes \"",@"text",nil];

    if (!Send(pair[0],value) || ![Receive(pair[1]) isEqual:value]) return 6;

    close(pair[0]); close(pair[1]);

    /* Exercise both locked previews and owner-started first-test builds.

     * Merely inspecting the button must never activate the deployment path. */

    BOOL expectedEnabled = profile && [[configuration objectForKey:@"installation_enabled"] boolValue];

    if ([startButton isEnabled] != expectedEnabled) return 7;

    for (NSButton *button in @[manageButton,rebuildButton,diagnosticsButton,efiAccessButton,debugButton])

        if ([button isHidden]) return 50;

    if ([rebuildButton action]!=@selector(rebuild:) || [manageButton action]!=@selector(manage:)

        || [debugButton action]!=@selector(debugReset:)) return 51;

    /* Even an enabled candidate cannot start work during --self-test. */

    [self start:nil];

    if (busy || listener != -1 || peer != -1 || sessionPath) return 9;

    [self manage:nil];

    if (busy || listener != -1 || peer != -1 || sessionPath) return 17;

    /* Repeated cancellation must restore every privileged control and clear

     * the stale progress display, using the actual worker wait/finish path. */

    for (unsigned attempt=0;attempt<3;attempt++) {

        [workerOperation release]; workerOperation=[@"manage" copy];

        [self setBusy:YES]; [self beginWorker:nil];

        if (busy || listener!=-1 || peer!=-1 || [activity isIndeterminate]

            || [activity doubleValue]!=0 || [activity isDisplayedWhenStopped]

            || [[status stringValue] rangeOfString:@"authorization cancelled"].location==NSNotFound) return 38;

        BOOL manageEnabled=profile && [[configuration objectForKey:@"management_enabled"] boolValue];

        for (NSButton *button in @[manageButton,rebuildButton,diagnosticsButton,efiAccessButton,debugButton])

            if ([button isEnabled]!=manageEnabled) return 39;

        if ([startButton isEnabled]!=expectedEnabled) return 40;

    }

    printf("Repeated authorization-cancel cleanup: all controls restored; progress cleared; no worker launched.\n");

    NSString *output = [[[NSProcessInfo processInfo] environment] objectForKey:@"DEPLOYER_UI_PREVIEW_PNG"];

    if (output) {

        [self savePreview:output];

        NSAlert *support = SupportAlert();

        if ([[support buttons] count] != 3

            || ![SupportURL(NSAlertFirstButtonReturn) isEqual:@"https://github.com/jaegermeister-dev/GoAwayBCA"]

            || ![SupportURL(NSAlertSecondButtonReturn) isEqual:@"https://www.reddit.com/r/GoAwayBCA/"]

            || SupportURL(NSAlertThirdButtonReturn)) return 33;

        [support layout]; SaveViewPreview([[support window] contentView],[output stringByAppendingString:@".support.png"]);

        for (NSString *kind in [NSArray arrayWithObjects:@"driver_storage",@"driver_mismatch",nil]) {

            NSAlert *choice=DriverChoiceAlert(kind,[kind isEqual:@"driver_storage"] ?

                @"The default folder supports automatic driver copying and later reuse. Custom saves are output only: copy them to Windows manually."

                : @"Saved files differ from their original download. Choose whether to use them anyway, move them to the Desktop and download again, delete that cache package and download again, or cancel.");

            if ([[choice buttons] count]!=4) return 37;

            [choice layout]; SaveViewPreview([[choice window] contentView],[output stringByAppendingFormat:@".%@.png",kind]);

        }

        if (!([window styleMask]&NSResizableWindowMask)

            || !NSEqualSizes([[window contentView] bounds].size,NSMakeSize(720,540))) return 34;

        NSRect fitted=FitWindowFrame(NSMakeRect(3000,2000,1900,1200),NSMakeRect(0,0,800,578));

        if (!NSContainsRect(NSMakeRect(0,0,800,578),fitted)) return 35;

        for (NSValue *size in [NSArray arrayWithObjects:[NSValue valueWithSize:NSMakeSize(640,500)],

                [NSValue valueWithSize:NSMakeSize(1000,740)],nil]) {

            [window setContentSize:[size sizeValue]]; [self layoutActionRow];

            for (NSView *child in [[window contentView] subviews])

                if (![child isHidden] && !NSContainsRect([[window contentView] bounds],[child frame])) return 36;

            [self savePreview:[output stringByAppendingFormat:@".%d-width.png",(int)[size sizeValue].width]];

        }

        [window setContentSize:NSMakeSize(720,540)]; [self layoutActionRow];

        /* Off-screen QA only: never change the user's system preference. The

         * normal GUI does not set an appearance; it inherits the host's. */

        Class appearanceClass = NSClassFromString(@"NSAppearance");

        if (appearanceClass && [window respondsToSelector:@selector(setAppearance:)]) {

            for (NSString *name in [NSArray arrayWithObjects:@"NSAppearanceNameAqua",@"NSAppearanceNameDarkAqua",nil]) {

                id appearance = [appearanceClass performSelector:@selector(appearanceNamed:) withObject:name];

                if (!appearance) continue;

                [window performSelector:@selector(setAppearance:) withObject:appearance];

                [appearanceClass performSelector:@selector(setCurrentAppearance:) withObject:appearance];

                [self savePreview:[output stringByAppendingFormat:@".%@.png",name]];

            }

        }

    }

    printf("Native UI, host profiles, payload integrity and plist IPC self-tests passed. Installer not run.\n");

    return 0;

}

- (void)savePreview:(NSString *)path {

    [window display];

    SaveViewPreview([window contentView], path);

}

@end



int main(int argc, char **argv) {

    NSAutoreleasePool *pool = [[NSAutoreleasePool alloc] init]; signal(SIGPIPE,SIG_IGN); umask(077);

    unsigned i; for(i=1;i<(unsigned)argc;i++) {

        if (!strcmp(argv[i],"--self-test")) SelfTest=YES;

        else if (!strcmp(argv[i],"--ui-simulation")) DemoMode=YES;

        else { fprintf(stderr,"Unknown argument\n"); return 2; }

    }

    if (geteuid()==0) { fprintf(stderr,"Run the GUI as an ordinary user.\n"); return 2; }

    Resources=[[[NSBundle mainBundle] resourcePath] retain];

    [NSApplication sharedApplication];

    DeployerUI *delegate=[[DeployerUI alloc] init];

    if (SelfTest) { int result=[delegate selfTest]; [pool drain]; return result; }

    [NSApp setDelegate:(id)delegate]; [NSApp run]; [pool drain]; return 0;

}

