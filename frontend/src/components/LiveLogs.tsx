import React, { useEffect, useState, useRef, useCallback } from 'react';
import { 
  Box, Card, CardContent, Typography, Button, 
  Select, MenuItem, FormControl, InputLabel,
  IconButton, Tooltip, useTheme, Snackbar, Alert
} from '@mui/material';
import { 
  DeleteSweep as ClearIcon, 
  VerticalAlignBottom as ScrollIcon,
  Pause as PauseIcon,
  PlayArrow as PlayIcon,
  ContentCopy as CopyIcon,
  Download as DownloadIcon
} from '@mui/icons-material';

interface LogEntry {
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
    return saved ? parseInt(saved, 10) : 500;
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
    const es = new EventSource('/api/logs/stream');
    eventSourceRef.current = es;

    es.onopen = () => {
      setIsConnected(true);
      reconnectDelayRef.current = 1000; // reset backoff on successful connection
    };

    es.onmessage = (event) => {
      if (isPausedRef.current) return;
      try {
        const entry: LogEntry = JSON.parse(event.data);
        setLogs((prev) => {
          const newLogs = [...prev, entry];
          const limit = limitLinesRef.current;
          return newLogs.length > limit ? newLogs.slice(-limit) : newLogs;
        });
      } catch (err) {
        console.error('Failed to parse log streaming message', err);
      }
    };

    es.onerror = () => {
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
      case 'DEBUG': return '#808080';
      case 'WARNING': return '#ffb74d';
      case 'ERROR':
      case 'CRITICAL': return '#ef5350';
      case 'INFO':
      default: return '#81c784';
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
            Realtime terminal logs piped directly from VeryDisco-MD.
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
                localStorage.setItem('logsLimit', String(val));
              }}
            >
              <MenuItem value={500}>500 lines</MenuItem>
              <MenuItem value={1000}>1000 lines</MenuItem>
              <MenuItem value={2000}>2000 lines</MenuItem>
              <MenuItem value={5000}>5000 lines</MenuItem>
            </Select>
          </FormControl>

          <Tooltip title={isPausedDisplay ? 'Resume logging stream' : 'Pause logging stream'}>
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

      <Card sx={{ bgcolor: '#0a090d', border: '1px solid #1a1924', borderRadius: 4 }}>
        <CardContent sx={{ p: 0, '&:last-child': { pb: 0 } }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, px: 2, py: 1.5, borderBottom: '1px solid #1a1924', bgcolor: '#121118' }}>
            <span style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#ef5350', display: 'inline-block' }} />
            <span style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#ffb74d', display: 'inline-block' }} />
            <span style={{ width: 12, height: 12, borderRadius: '50%', backgroundColor: '#81c784', display: 'inline-block' }} />
            <Typography variant="body2" sx={{ ml: 2, fontFamily: 'JetBrains Mono', color: '#a5a4b1', fontSize: '0.8rem' }}>
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
              '&::-webkit-scrollbar-track': { background: '#0a090d' },
              '&::-webkit-scrollbar-thumb': { background: '#2c2b3c', borderRadius: '4px' },
              '&::-webkit-scrollbar-thumb:hover': { background: '#3c3b4d' },
            }}
          >
            {filteredLogs.length === 0 ? (
              <Typography sx={{ fontFamily: 'JetBrains Mono', color: '#626070', fontSize: '0.9rem', fontStyle: 'italic' }}>
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
                    borderBottom: { xs: '1px dashed rgba(255,255,255,0.06)', md: 'none' }
                  }}
                >
                  <Box display="flex" alignItems="center" gap={1} flexShrink={0}>
                    <Typography component="span" sx={{ fontFamily: 'inherit', fontSize: 'inherit', color: '#7a798c', userSelect: 'none' }}>
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
                  <Typography component="span" sx={{ fontFamily: 'inherit', fontSize: 'inherit', color: '#e5e5e9', whiteSpace: 'pre-wrap' }}>
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
    <span style={{
      width: 8, height: 8, borderRadius: '50%',
      backgroundColor: isConnected ? '#81c784' : '#ef5350',
      display: 'inline-block',
      boxShadow: isConnected ? '0 0 8px #81c784' : '0 0 8px #ef5350'
    }} />
    <Typography variant="caption" sx={{ fontWeight: 600, color: 'text.secondary' }}>
      {isConnected ? 'LIVE' : 'RECONNECTING...'}
    </Typography>
  </Box>
);

export default LiveLogs;
