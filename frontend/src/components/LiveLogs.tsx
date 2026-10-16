import React, { useEffect, useState, useRef, useCallback } from 'react';
import { 
  Box, Card, CardContent, Typography, Button, 
  Select, MenuItem, FormControl, InputLabel,
  IconButton, Tooltip, useTheme, Snackbar, Alert
} from '@mui/material';
import ClearIcon from '@mui/icons-material/DeleteSweep';
import ScrollIcon from '@mui/icons-material/VerticalAlignBottom';
import PauseIcon from '@mui/icons-material/Pause';
import PlayIcon from '@mui/icons-material/PlayArrow';
import CopyIcon from '@mui/icons-material/ContentCopy';
import DownloadIcon from '@mui/icons-material/Download';
import { alpha } from '@mui/material/styles';

interface LogEntry {
  id?: number;
  timestamp: string;
  level: string;
  message: string;
  run_id: number | null;
}

export const LiveLogs: React.FC = () => {
  const theme = useTheme();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [filterLevel, setFilterLevel] = useState<string>('ALL');
  const [limitLines, setLimitLines] = useState<number>(() => {
    const saved = localStorage.getItem('logsLimit');
    const parsed = saved ? parseInt(saved, 10) : 0;
    return [0, 500, 1000, 2000, 5000].includes(parsed) ? parsed : 0;
  });
  const limitLinesRef = useRef<number>(limitLines);
  useEffect(() => {
    limitLinesRef.current = limitLines;
  }, [limitLines]);
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [snackbar, setSnackbar] = useState<{ open: boolean; message: string }>({ open: false, message: '' });

  // Use refs to avoid stale closures without triggering reconnections
  const isPausedRef = useRef<boolean>(false);
  const [isPausedDisplay, setIsPausedDisplay] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const logsContainerRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectDelayRef = useRef<number>(1000);
  const lastEventIdRef = useRef(0);

  const scrollToBottom = () => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  useEffect(() => {
    if (!isPausedRef.current) {
      scrollToBottom();
    }
  }, [logs]);

  const connectStream = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }

    setIsConnected(false);
    const es = new EventSource(`/api/logs/stream?limit=${limitLinesRef.current}&after=${lastEventIdRef.current}`);
    eventSourceRef.current = es;

    es.onopen = () => {
      if (eventSourceRef.current !== es) return;
      setIsConnected(true);
      reconnectDelayRef.current = 1000; // reset backoff on successful connection
    };

    es.onmessage = (event) => {
      if (eventSourceRef.current !== es) return;
      try {
        const entry: LogEntry = JSON.parse(event.data);
        lastEventIdRef.current = Number(event.lastEventId) || entry.id || lastEventIdRef.current;
        setLogs((prev) => {
          const newLogs = [...prev, entry];
          const limit = limitLinesRef.current;
          return limit > 0 && newLogs.length > limit ? newLogs.slice(-limit) : newLogs;
        });
      } catch (err) {
        console.error('Failed to parse log streaming message', err);
      }
    };

    es.onerror = () => {
      if (eventSourceRef.current !== es) return;
      setIsConnected(false);
      es.close();
      eventSourceRef.current = null;
      // Exponential backoff reconnect
      const delay = Math.min(reconnectDelayRef.current, 30000);
      reconnectDelayRef.current = delay * 2;
      reconnectTimerRef.current = setTimeout(() => connectStream(), delay);
    };
  }, []);

  useEffect(() => {
    connectStream();
    return () => {
      if (eventSourceRef.current) eventSourceRef.current.close();
      eventSourceRef.current = null;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    };
  }, [connectStream]);

  const togglePause = () => {
    isPausedRef.current = !isPausedRef.current;
    setIsPausedDisplay(isPausedRef.current);
  };

  const handleClear = () => setLogs([]);

  const formatLogsText = (): string =>
    logs.map(log => `[${log.timestamp}] [${log.level.toUpperCase()}] ${log.message}`).join('\n');

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(formatLogsText());
      setSnackbar({ open: true, message: 'Logs copied to clipboard!' });
    } catch {
      setSnackbar({ open: true, message: 'Failed to copy — try the Download button instead.' });
    }
  };

  const handleDownload = () => {
    const blob = new Blob([formatLogsText()], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `verydisco_logs_${new Date().toISOString().slice(0, 10)}.txt`;
    link.click();
    URL.revokeObjectURL(url);
  };

  const getLogColor = (level: string) => {
    switch (level.toUpperCase()) {
      case 'DEBUG': return theme.palette.text.secondary;
      case 'WARNING': return theme.palette.warning.main;
      case 'ERROR':
      case 'CRITICAL': return theme.palette.error.main;
      case 'INFO':
      default: return theme.palette.success.main;
    }
  };

  const filteredLogs = logs.filter(log =>
    filterLevel === 'ALL' || log.level.toUpperCase() === filterLevel
  );

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box display="flex" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={2}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800 }}>Live Logs</Typography>
          <Typography variant="body2" color="text.secondary">
            Logs since application startup, followed by live updates.
          </Typography>
        </Box>
        <Box display="flex" alignItems="center" gap={2} flexWrap="wrap">
          <ChipStatus isConnected={isConnected} />
          
          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Filter Level</InputLabel>
            <Select value={filterLevel} label="Filter Level" onChange={(e) => setFilterLevel(e.target.value)}>
              <MenuItem value="ALL">All Levels</MenuItem>
              <MenuItem value="DEBUG">DEBUG</MenuItem>
              <MenuItem value="INFO">INFO</MenuItem>
              <MenuItem value="WARNING">WARNING</MenuItem>
              <MenuItem value="ERROR">ERROR</MenuItem>
            </Select>
          </FormControl>

          <FormControl size="small" sx={{ minWidth: 120 }}>
            <InputLabel>Logs History</InputLabel>
            <Select 
              value={limitLines} 
              label="Logs History" 
              onChange={(e) => {
                const val = Number(e.target.value);
                setLimitLines(val);
                limitLinesRef.current = val;
                lastEventIdRef.current = 0;
                setLogs([]);
                if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
                connectStream();
                localStorage.setItem('logsLimit', String(val));
              }}
            >
              <MenuItem value={0}>All since startup</MenuItem>
              <MenuItem value={500}>500 lines</MenuItem>
              <MenuItem value={1000}>1000 lines</MenuItem>
              <MenuItem value={2000}>2000 lines</MenuItem>
              <MenuItem value={5000}>5000 lines</MenuItem>
            </Select>
          </FormControl>

          <Tooltip title={isPausedDisplay ? 'Resume auto-scroll' : 'Pause auto-scroll (logs keep loading)'}>
            <IconButton color="primary" onClick={togglePause} sx={{ border: '1px solid', borderColor: 'divider' }}>
              {isPausedDisplay ? <PlayIcon /> : <PauseIcon />}
            </IconButton>
          </Tooltip>

          <Tooltip title="Scroll to bottom">
            <IconButton color="primary" onClick={scrollToBottom} sx={{ border: '1px solid', borderColor: 'divider' }}>
              <ScrollIcon />
            </IconButton>
          </Tooltip>

          <Button variant="outlined" color="primary" startIcon={<CopyIcon />} onClick={handleCopy} disabled={logs.length === 0}>Copy</Button>
          <Button variant="outlined" color="primary" startIcon={<DownloadIcon />} onClick={handleDownload} disabled={logs.length === 0}>Download</Button>
          <Button variant="outlined" color="error" startIcon={<ClearIcon />} onClick={handleClear}>Clear</Button>
        </Box>
      </Box>

      <Card sx={{ bgcolor: 'background.paper', borderColor: 'divider', borderRadius: 4 }}>
        <CardContent sx={{ p: 0, '&:last-child': { pb: 0 } }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 1.5, borderBottom: '1px solid', borderColor: 'divider', bgcolor: 'action.hover' }}>
            <Box aria-hidden="true" sx={{ width: 12, height: 12, flexShrink: 0, borderRadius: '50%', bgcolor: 'error.main' }} />
            <Box aria-hidden="true" sx={{ width: 12, height: 12, flexShrink: 0, borderRadius: '50%', bgcolor: 'warning.main' }} />
            <Box aria-hidden="true" sx={{ width: 12, height: 12, flexShrink: 0, borderRadius: '50%', bgcolor: 'success.main' }} />
            <Typography variant="body2" sx={{ ml: { xs: 0.5, sm: 2 }, fontFamily: 'JetBrains Mono, monospace', color: 'text.secondary', fontSize: '0.8rem', overflowWrap: 'anywhere' }}>
              verydisco@homelab:~ $ tail -f log.stream
            </Typography>
          </Box>
          <Box
            ref={logsContainerRef}
            sx={{
              height: '60vh',
              overflowY: 'auto',
              p: 3,
              display: 'flex',
              flexDirection: 'column',
              gap: 0.5,
              '&::-webkit-scrollbar': { width: '8px' },
              '&::-webkit-scrollbar-track': { background: theme.palette.background.paper },
              '&::-webkit-scrollbar-thumb': { background: alpha(theme.palette.primary.main, 0.3), borderRadius: '4px' },
              '&::-webkit-scrollbar-thumb:hover': { background: alpha(theme.palette.primary.main, 0.5) },
            }}
          >
            {filteredLogs.length === 0 ? (
              <Typography sx={{ fontFamily: 'JetBrains Mono, monospace', color: 'text.secondary', fontSize: '0.9rem', fontStyle: 'italic' }}>
                -- no log outputs recorded yet --
              </Typography>
            ) : (
              filteredLogs.map((log, idx) => (
                <Box 
                  key={idx} 
                  sx={{ 
                    fontFamily: 'JetBrains Mono, monospace', 
                    fontSize: { xs: '0.78rem', md: '0.85rem' }, 
                    display: 'flex', 
                    flexDirection: { xs: 'column', md: 'row' },
                    gap: { xs: 0.5, md: 1.5 }, 
                    alignItems: { xs: 'stretch', md: 'flex-start' }, 
                    lineHeight: '1.5', 
                    wordBreak: 'break-word',
                    py: { xs: 0.75, md: 0.25 },
                    borderBottom: { xs: '1px dashed', md: 'none' },
                    borderColor: 'divider',
                  }}
                >
                  <Box display="flex" alignItems="center" gap={1} flexShrink={0}>
                    <Typography component="span" sx={{ fontFamily: 'inherit', fontSize: 'inherit', color: 'text.secondary', userSelect: 'none' }}>
                      {new Date(log.timestamp).toLocaleTimeString()}
                    </Typography>
                    <Typography component="span" sx={{ fontFamily: 'inherit', fontSize: 'inherit', color: getLogColor(log.level), fontWeight: 700 }}>
                      [{log.level.toUpperCase()}]
                    </Typography>
                    {log.run_id && (
                      <Typography component="span" sx={{ fontFamily: 'inherit', fontSize: 'inherit', color: theme.palette.primary.main, fontWeight: 600 }}>
                        [Run #{log.run_id}]
                      </Typography>
                    )}
                  </Box>
                  <Typography component="span" sx={{ fontFamily: 'inherit', fontSize: 'inherit', color: 'text.primary', whiteSpace: 'pre-wrap' }}>
                    {log.message}
                  </Typography>
                </Box>
              ))
            )}
            <div ref={logsEndRef} />
          </Box>
        </CardContent>
      </Card>

      <Snackbar
        open={snackbar.open}
        autoHideDuration={3000}
        onClose={() => setSnackbar(s => ({ ...s, open: false }))}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert severity="success" variant="filled" onClose={() => setSnackbar(s => ({ ...s, open: false }))}>
          {snackbar.message}
        </Alert>
      </Snackbar>
    </Box>
  );
};

const ChipStatus: React.FC<{ isConnected: boolean }> = ({ isConnected }) => (
  <Box display="flex" alignItems="center" gap={1} sx={{ mr: 1 }}>
    <Box component="span" sx={{
      width: 8, height: 8, borderRadius: '50%',
      bgcolor: isConnected ? 'success.main' : 'warning.main',
      display: 'inline-block',
      boxShadow: theme => `0 0 0 3px ${alpha(isConnected ? theme.palette.success.main : theme.palette.warning.main, 0.12)}`,
    }} />
    <Typography variant="caption" sx={{ fontWeight: 600, color: 'text.secondary' }}>
      {isConnected ? 'LIVE' : 'RECONNECTING...'}
    </Typography>
  </Box>
);

export default LiveLogs;
