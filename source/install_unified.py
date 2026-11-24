"""One selection flow; keep the accepted Windows 7 component intact."""
from install_backtoseven import BackToSevenInstaller
from install_multiboot import MultiBootInstaller, FAMILIES


def uses_backtoseven(model, edition):
    """Hardware-specific support never spills into another OS or model."""
    if edition.get('family') not in FAMILIES:
        raise ValueError('Select a supported Windows edition')
    return (edition['family'] == 'Windows 7'
            and model in ('MacBookPro11,4', 'MacBookPro11,5'))


class UnifiedInstaller(BackToSevenInstaller):
    require_supported_image=True
    def execute(self):
        # The source is inspected once, with a model confirmation and actual
        # firmware/CPU checks before either engine downloads or writes anything.
        # This context is produced here, not accepted from GUI configuration.
        selection = self.prepare_installation_selection(early_scan=True)
        self.unified_menu=True
        self._unified_selection = selection
        try:
            if uses_backtoseven(selection['model'], selection['edition']):
                return BackToSevenInstaller.execute(self)
            return MultiBootInstaller.execute(self)
        finally:
            self.__dict__.pop('_unified_selection', None)
