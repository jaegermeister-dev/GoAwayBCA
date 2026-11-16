/* Read-only native UI. Never launches a root browser or accepts a boot-mode
 * override. External links are exact, curated documentation URLs. */
static BOOL FirmwareGuideURLAllowed(NSString *url) {
    return [url isKindOfClass:[NSString class]] &&
        [[NSArray arrayWithObjects:
          @"https://blog.andersonshatch.com/2009/11/08/select-cd-rom-boot-type-prompt-during-vista-server-2008-r2-windows-7-x64-boot-on-macbook/",
          @"https://virtuallyfun.com/2018/12/26/installing-windows-10-pro-onto-an-older-mac-pro/",
          @"https://www.rodsbooks.com/refind/bootmode.html",nil] containsObject:url];
}
static NSArray *FirmwareGuideLinks(NSDictionary *message) {
    NSMutableArray *safe = [NSMutableArray array];
    id links = [message objectForKey:@"links"];
    if (![links isKindOfClass:[NSArray class]]) return safe;
    for (id link in links) {
        if ([link isKindOfClass:[NSDictionary class]] && FirmwareGuideURLAllowed([link objectForKey:@"url"])
            && [[link objectForKey:@"title"] isKindOfClass:[NSString class]]) [safe addObject:link];
    }
    return safe;
}
static BOOL FirmwareCanRetry(NSDictionary *message) {
    return [[message objectForKey:@"code"] isEqual:@"firmware-architecture-unknown"];
}
static BOOL FirmwareHasGuides(NSDictionary *message) {
    return !FirmwareCanRetry(message) && [FirmwareGuideLinks(message) count] > 0;
}
static NSString *FirmwareDialogAction(NSDictionary *message, NSInteger response) {
    if (FirmwareCanRetry(message)) return response==NSAlertFirstButtonReturn ? @"retry" : @"cancel";
    if (FirmwareHasGuides(message)) {
        if (response==NSAlertFirstButtonReturn) return @"guide";
        return response==NSAlertSecondButtonReturn ? @"choose-image" : @"cancel";
    }
    return response==NSAlertFirstButtonReturn ? @"choose-image" : @"cancel";
}
static NSAlert *FirmwareBlockAlert(NSDictionary *message) {
    NSAlert *alert = [[[NSAlert alloc] init] autorelease];
    [alert setMessageText:@"Windows EFI compatibility"];
    [alert setInformativeText:[message objectForKey:@"text"] ?: @"EFI compatibility could not be confirmed."];
    if (FirmwareCanRetry(message)) [alert addButtonWithTitle:@"Retry detection"];
    else {
        if (FirmwareHasGuides(message)) [alert addButtonWithTitle:@"Read selected guide"];
        [alert addButtonWithTitle:@"Choose another Windows image"];
    }
    [alert addButtonWithTitle:@"Cancel"];
    if (FirmwareHasGuides(message)) {
        NSView *box = [[[NSView alloc] initWithFrame:NSMakeRect(0,0,520,60)] autorelease];
        NSPopUpButton *guides = [[[NSPopUpButton alloc] initWithFrame:NSMakeRect(0,20,520,28) pullsDown:NO] autorelease];
        [guides setTag:601];
        for (NSDictionary *link in FirmwareGuideLinks(message)) {
            [guides addItemWithTitle:[link objectForKey:@"title"]];
            [[guides lastItem] setRepresentedObject:[link objectForKey:@"url"]];
            [[guides lastItem] setToolTip:[link objectForKey:@"scope"]];
        }
        [box addSubview:guides]; [alert setAccessoryView:box];
    }
    for (NSButton *button in [alert buttons]) [button setKeyEquivalent:@""];
    [[[alert buttons] lastObject] setKeyEquivalent:@"\r"];
    return alert;
}
