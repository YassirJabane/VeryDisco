import React from 'react';
import { Box, Dialog, DialogContent, InputAdornment, List, ListItemButton, ListItemIcon, ListItemText, TextField, Typography } from '@mui/material';
import SearchIcon from '@mui/icons-material/SearchRounded';
import GoIcon from '@mui/icons-material/NorthWestRounded';
import { filterDestinations, NavigationDestination } from '../navigation';

export default function CommandPalette({ open, onClose, destinations, onNavigate }: {
  open: boolean; onClose: () => void; destinations: NavigationDestination[]; onNavigate: (id: string) => void;
}) {
  const [query, setQuery] = React.useState('');
  const [selected, setSelected] = React.useState(0);
  React.useEffect(() => { if (open) { setQuery(''); setSelected(0); } }, [open]);
  const results = filterDestinations(destinations, query);
  const choose = (id: string) => { onNavigate(id); onClose(); };
  const active = Math.min(selected, Math.max(0, results.length - 1));
  React.useEffect(() => {
    if (open && results[active]) document.getElementById(`command-${results[active].id}`)?.scrollIntoView?.({ block: 'nearest' });
  }, [open, active, query]);
  return <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="command-title"
    PaperProps={{ sx: { alignSelf: 'flex-start', mt: { xs: 3, sm: '14vh' }, mx: 2, overflow: 'hidden', borderRadius: 4 } }}>
    <Box sx={{ px: 3, pt: 2.5 }}><Typography id="command-title" variant="overline" color="text.secondary">Your workspace, one shortcut away</Typography></Box>
    <DialogContent sx={{ p: 2, pt: 1 }}>
      <TextField autoFocus fullWidth placeholder="Where would you like to go?" value={query}
        onChange={event => { setQuery(event.target.value); setSelected(0); }}
        inputProps={{ 'aria-label': 'Search pages', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': true,
          'aria-controls': 'command-results', 'aria-activedescendant': results.length ? `command-${results[active].id}` : undefined }}
        InputProps={{ startAdornment: <InputAdornment position="start"><SearchIcon /></InputAdornment> }}
        onKeyDown={event => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setSelected(current => results.length ? (current + (event.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length : 0);
          } else if (event.key === 'Enter' && results[active]) { event.preventDefault(); choose(results[active].id); }
        }} />
      <List id="command-results" role="listbox" aria-label="Pages" sx={{ mt: 1, maxHeight: '46vh', overflowY: 'auto' }}>
        {results.map((item, index) => <ListItemButton key={item.id} id={`command-${item.id}`} role="option"
          aria-selected={index === active} selected={index === active} onClick={() => choose(item.id)}
          sx={{ borderRadius: 2, minHeight: 54, mb: .5 }}>
          <ListItemIcon sx={{ minWidth: 42, color: index === active ? 'primary.main' : 'text.secondary' }}>{item.icon}</ListItemIcon>
          <ListItemText primary={item.text} secondary={item.group} primaryTypographyProps={{ fontWeight: 650 }} />
          {index === active && <GoIcon fontSize="small" sx={{ color: 'primary.main' }} />}
        </ListItemButton>)}
        {!results.length && <Box role="status" sx={{ py: 5, textAlign: 'center' }}><Typography fontWeight={650}>No matching pages</Typography><Typography variant="body2" color="text.secondary">Try “lyrics”, “library” or “settings”.</Typography></Box>}
      </List>
    </DialogContent>
    <Box sx={{ px: 3, py: 1.5, display: 'flex', justifyContent: 'space-between', bgcolor: 'action.hover', borderTop: '1px solid', borderColor: 'divider' }}>
      <Typography variant="caption" color="text.secondary">↑ ↓ to browse · Enter to open</Typography><Typography variant="caption" color="text.secondary">Esc to close</Typography>
    </Box>
  </Dialog>;
}
