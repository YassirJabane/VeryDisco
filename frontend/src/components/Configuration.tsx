import React, { useEffect, useState, useRef } from 'react';
import { 
  Box, Card, CardContent, Typography, TextField, Button, Grid, 
  Switch, FormControlLabel, MenuItem, Select, FormControl, InputLabel,
  Tabs, Tab, Alert, CircularProgress, Divider, useTheme, Chip,
  IconButton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Tooltip,
  Checkbox, List, ListItem, ListItemText, Dialog, DialogTitle, DialogContent, DialogActions
} from '@mui/material';
import { Save as SaveIcon, Edit as EditIcon, Settings as SettingsIcon, CheckCircle as CheckCircleIcon, Error as ErrorIcon, WifiTethering as TestIcon, ArrowUpward as ArrowUpwardIcon, ArrowDownward as ArrowDownwardIcon, Add as AddIcon, Delete as DeleteIcon, DragIndicator as DragIcon, CloudDownload as ImportIcon, AdminPanelSettings as AdminIcon, Tune as TuneIcon } from '@mui/icons-material';
import { apiService, GetConfigResponse, ConfigData } from '../api';
import { useAuth } from '../context/AuthContext';

interface QualityProfile {
  format: string;
  min_bitrate: number;
  max_bitrate: number;
  bit_depth: number;
  sample_rate: number;
}

interface StandardQuality {
  id: string;
  name: string;
  format: string;
  min_bitrate: number;
  max_bitrate: number;
  bit_depth: number;
  sample_rate: number;
  checked: boolean;
}

const DEFAULT_LADDER: Omit<StandardQuality, 'checked'>[] = [
  { id: 'flac_24', name: 'FLAC 24bit', format: 'flac', min_bitrate: 0, max_bitrate: 0, bit_depth: 24, sample_rate: 0 },
  { id: 'flac', name: 'FLAC', format: 'flac', min_bitrate: 0, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'wav', name: 'WAV', format: 'wav', min_bitrate: 0, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'alac_24', name: 'ALAC 24bit', format: 'alac', min_bitrate: 0, max_bitrate: 0, bit_depth: 24, sample_rate: 0 },
  { id: 'alac', name: 'ALAC', format: 'alac', min_bitrate: 0, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'ape', name: 'APE', format: 'ape', min_bitrate: 0, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'mp3_320', name: 'MP3 320', format: 'mp3', min_bitrate: 320, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'mp3_256', name: 'MP3 256', format: 'mp3', min_bitrate: 256, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'mp3_192', name: 'MP3 192', format: 'mp3', min_bitrate: 192, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'aac_320', name: 'AAC 320', format: 'm4a', min_bitrate: 320, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'aac_256', name: 'AAC 256', format: 'm4a', min_bitrate: 256, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'ogg', name: 'OGG Vorbis', format: 'ogg', min_bitrate: 192, max_bitrate: 0, bit_depth: 0, sample_rate: 0 },
  { id: 'opus', name: 'OPUS', format: 'opus', min_bitrate: 0, max_bitrate: 0, bit_depth: 0, sample_rate: 0 }
];

const matchProfileToStandard = (prof: any): StandardQuality => {
  const matched = DEFAULT_LADDER.find(
    item =>
      item.format === prof.format &&
      item.min_bitrate === (prof.min_bitrate || 0) &&
      item.max_bitrate === (prof.max_bitrate || 0) &&
      item.bit_depth === (prof.bit_depth || 0) &&
      item.sample_rate === (prof.sample_rate || 0)
  );

  if (matched) {
    return { ...matched, checked: true };
  }

  let name = `${prof.format.toUpperCase()}`;
  if (prof.min_bitrate) name += ` ${prof.min_bitrate}kbps`;
  if (prof.bit_depth) name += ` ${prof.bit_depth}bit`;
  if (prof.sample_rate) name += ` ${prof.sample_rate}Hz`;

  const id = `custom_${prof.format}_${prof.min_bitrate || 0}_${prof.bit_depth || 0}_${prof.sample_rate || 0}`;

  return {
    id,
    name,
    format: prof.format,
    min_bitrate: prof.min_bitrate || 0,
    max_bitrate: prof.max_bitrate || 0,
    bit_depth: prof.bit_depth || 0,
    sample_rate: prof.sample_rate || 0,
    checked: true
  };
};

export const Configuration: React.FC = () => {
  const theme = useTheme();
  const { user } = useAuth();
  const [tabValue, setTabValue] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [configResponse, setConfigResponse] = useState<GetConfigResponse | null>(null);

  // Admin: user management
  const [users, setUsers] = useState<any[]>([]);
  const [usersLoading, setUsersLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [importMsg, setImportMsg] = useState<string | null>(null);
  const [importErr, setImportErr] = useState<string | null>(null);
  // Per-row editable paths: { [userId]: { music_dir, playlist_dir, saving } }
  const [userEdits, setUserEdits] = useState<Record<string, { music_dir: string; playlist_dir: string; saving: boolean }>>();
  
  // Per-row features configuration dialog
  const [featureUser, setFeatureUser] = useState<any | null>(null);
  const [featureEdits, setFeatureEdits] = useState<Record<string, boolean>>({
    starred_sync: true,
    listenbrainz_sync: true,
    discovery: true,
    album_downloads: true,
  });
  const [featureSaving, setFeatureSaving] = useState(false);
  
  const [slskdUrl, setSlskdUrl] = useState('http://slskd:5030');
  const [slskdApiKey, setSlskdApiKey] = useState('');
  const [slskdDownloadsDir, setSlskdDownloadsDir] = useState('/slskd_downloads');
  const [audioQualityPreset, setAudioQualityPreset] = useState('lossless');
  const [audioQualityLadder, setAudioQualityLadder] = useState<StandardQuality[]>([]);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  
  // Custom format form helper states
  const [customFormat, setCustomFormat] = useState('flac');
  const [customMinBitrate, setCustomMinBitrate] = useState(0);
  const [customBitDepth, setCustomBitDepth] = useState(0);
  const [customSampleRate, setCustomSampleRate] = useState(0);

  const [provider, setProvider] = useState('lrclib');
  const [lyricsUrl, setLyricsUrl] = useState('https://lrclib.net');

  const [dailyTime, setDailyTime] = useState('04:00');
  const [weeklyTime, setWeeklyTime] = useState('04:00');
  const [weeklyDay, setWeeklyDay] = useState('tue');
  const [fileChecksTime, setFileChecksTime] = useState('04:00');
  const [fileChecksDay, setFileChecksDay] = useState('sun');
  const [runOnStartup, setRunOnStartup] = useState(true);
  const [batchSize, setBatchSize] = useState(5);
  const [maxAttempts, setMaxAttempts] = useState(3);

  const [weeklyOutputDir, setWeeklyOutputDir] = useState('/data/weekly/current');

  const [isDirty, setIsDirty] = useState(false);

  const [navidromeUrl, setNavidromeUrl] = useState('');
  const [navidromeUsername, setNavidromeUsername] = useState('');
  const [navidromePassword, setNavidromePassword] = useState('');
  const [acoustidApiKey, setAcoustidApiKey] = useState('');

  const [httpTimeout, setHttpTimeout] = useState(20);
  const [searchTimeout, setSearchTimeout] = useState(30);
  const [downloadTimeout, setDownloadTimeout] = useState(240);

  const [logLevel, setLogLevel] = useState('INFO');

  // RAW editor state
  const [rawYaml, setRawYaml] = useState('');

  // Connection test states
  type TestState = { status: 'idle' | 'testing' | 'ok' | 'error'; message: string };
  const [lbTest, setLbTest] = useState<TestState>({ status: 'idle', message: '' });
  const [slskdTest, setSlskdTest] = useState<TestState>({ status: 'idle', message: '' });
  const [ndTest, setNdTest] = useState<TestState>({ status: 'idle', message: '' });

  // Alerts
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleToggleChecked = (id: string) => {
    const updated = audioQualityLadder.map(item => {
      if (item.id === id) {
        return { ...item, checked: !item.checked };
      }
      return item;
    });
    
    // Auto-order: checked items first, followed by unchecked
    const checked = updated.filter(x => x.checked);
    const unchecked = updated.filter(x => !x.checked);
    setAudioQualityLadder([...checked, ...unchecked]);
    setIsDirty(true);
  };

  const handleDragStart = (index: number) => {
    setDraggedIndex(index);
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === index) return;
    
    const updated = [...audioQualityLadder];
    const draggedItem = updated[draggedIndex];
    updated.splice(draggedIndex, 1);
    updated.splice(index, 0, draggedItem);
    
    setDraggedIndex(index);
    setAudioQualityLadder(updated);
    setIsDirty(true);
  };

  const handleDragEnd = () => {
    setDraggedIndex(null);
  };

  const handleAddCustomQuality = () => {
    const duplicate = audioQualityLadder.find(
      x =>
        x.format === customFormat &&
        x.min_bitrate === customMinBitrate &&
        x.bit_depth === customBitDepth &&
        x.sample_rate === customSampleRate
    );
    if (duplicate) {
      if (!duplicate.checked) {
        handleToggleChecked(duplicate.id);
      }
      return;
    }

    let name = `${customFormat.toUpperCase()}`;
    if (customMinBitrate) name += ` ${customMinBitrate}kbps`;
    if (customBitDepth) name += ` ${customBitDepth}bit`;
    if (customSampleRate) name += ` ${customSampleRate}Hz`;

    const id = `custom_${customFormat}_${customMinBitrate}_${customBitDepth}_${customSampleRate}`;
    
    const newItem: StandardQuality = {
      id,
      name,
      format: customFormat,
      min_bitrate: customMinBitrate,
      max_bitrate: 0,
      bit_depth: customBitDepth,
      sample_rate: customSampleRate,
      checked: true
    };

    setAudioQualityLadder([newItem, ...audioQualityLadder]);
    setIsDirty(true);

    // Reset inputs
    setCustomMinBitrate(0);
    setCustomBitDepth(0);
    setCustomSampleRate(0);
  };

  const handleResetToLossless = () => {
    const losslessIds = ['flac_24', 'flac', 'wav', 'alac_24', 'alac', 'ape'];
    const updated = DEFAULT_LADDER.map(item => ({
      ...item,
      checked: losslessIds.includes(item.id)
    }));
    const checked = updated.filter(x => x.checked);
    const unchecked = updated.filter(x => !x.checked);
    setAudioQualityLadder([...checked, ...unchecked]);
    setIsDirty(true);
  };

  const handleResetToStorageSaver = () => {
    const storageSaverIds = ['mp3_320', 'mp3_256', 'mp3_192', 'aac_320', 'aac_256', 'ogg'];
    const updated = DEFAULT_LADDER.map(item => ({
      ...item,
      checked: storageSaverIds.includes(item.id)
    }));
    const checked = updated.filter(x => x.checked);
    const unchecked = updated.filter(x => !x.checked);
    setAudioQualityLadder([...checked, ...unchecked]);
    setIsDirty(true);
  };

  const fetchConfig = async () => {
    try {
      setLoading(true);
      const data = await apiService.getConfig();
      setConfigResponse(data);
      setRawYaml(data.raw_yaml);
      
      if (data.parsed) {
        const p = data.parsed;
        
        setSlskdUrl(p.slskd.base_url || 'http://slskd:5030');
        setSlskdApiKey(p.slskd.api_key || '');
        setSlskdDownloadsDir(p.slskd.downloads_dir || '/slskd_downloads');
        setAudioQualityPreset(p.slskd.audio_quality?.preset || 'lossless');
        const rawProfiles = (p.slskd.audio_quality as any)?.custom_profiles || [];
        const parsedProfiles: StandardQuality[] = rawProfiles.map(matchProfileToStandard);
        const checkedIds = new Set(parsedProfiles.map((x: StandardQuality) => x.id));
        const unchecked = DEFAULT_LADDER.filter((x: Omit<StandardQuality, 'checked'>) => !checkedIds.has(x.id)).map((x: Omit<StandardQuality, 'checked'>) => ({ ...x, checked: false }));
        setAudioQualityLadder([...parsedProfiles, ...unchecked]);

        setProvider(p.lyrics?.provider || 'lrclib');
        setLyricsUrl(p.lyrics?.base_url || 'https://lrclib.net');

        setDailyTime(p.schedule?.daily_time || '04:00');
        setWeeklyTime(p.schedule?.weekly_time || '04:00');
        setWeeklyDay(p.schedule?.weekly_day || 'tue');
        setFileChecksTime(p.schedule?.file_checks_time || '04:00');
        setFileChecksDay(p.schedule?.file_checks_day || 'sun');
        setRunOnStartup(p.schedule?.run_on_startup !== false);
        setBatchSize(p.schedule?.batch_size || 5);
        setMaxAttempts(p.schedule?.max_candidate_attempts || 3);

        setWeeklyOutputDir(p.paths?.weekly_output_dir || '/data/weekly/current');

        setNavidromeUrl(p.navidrome?.url || '');
        setNavidromeUsername(p.navidrome?.username || '');
        setNavidromePassword(p.navidrome?.password || '');
        setAcoustidApiKey(p.acoustid?.api_key || '');

        setHttpTimeout(p.timeouts?.http_seconds || 20);
        setSearchTimeout(p.timeouts?.search_seconds || 30);
        setDownloadTimeout(p.timeouts?.download_seconds || 240);

        setLogLevel(p.log_level || 'INFO');
      }
      setIsDirty(false);
    } catch (e: any) {
      setErrorMsg("Failed to read configuration from API.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConfig();
  }, []);

  // Load users list (admin only)
  useEffect(() => {
    if (!user?.isAdmin) return;
    setUsersLoading(true);
    apiService.getUsers()
      .then(data => {
        setUsers(data.users);
        // Initialise per-row edit state from server data
        const edits: Record<string, { music_dir: string; playlist_dir: string; saving: boolean }> = {};
        data.users.forEach((u: any) => {
          edits[u.id] = { music_dir: u.music_dir || '', playlist_dir: u.playlist_dir || '', saving: false };
        });
        setUserEdits(edits);
      })
      .catch(() => {})
      .finally(() => setUsersLoading(false));
  }, [user?.isAdmin]);

  const handleSaveUserPaths = async (userId: string) => {
    const edit = userEdits?.[userId];
    if (!edit) return;
    setUserEdits(prev => ({ ...prev!, [userId]: { ...prev![userId], saving: true } }));
    try {
      await apiService.updateUserPaths(userId, edit.music_dir, edit.playlist_dir);
      setUsers(prev => prev.map(u => u.id === userId ? { ...u, music_dir: edit.music_dir, playlist_dir: edit.playlist_dir } : u));
      setImportMsg(`Paths saved for user.`);
      setTimeout(() => setImportMsg(null), 3000);
    } catch (err: any) {
      setImportErr(err?.response?.data?.detail || 'Failed to save paths.');
    } finally {
      setUserEdits(prev => ({ ...prev!, [userId]: { ...prev![userId], saving: false } }));
    }
  };

  const handleOpenFeatures = (u: any) => {
    setFeatureUser(u);
    setFeatureEdits(u.enabled_features || {
      starred_sync: true,
      listenbrainz_sync: true,
      discovery: true,
      album_downloads: true,
    });
  };

  const handleSaveFeatures = async () => {
    if (!featureUser) return;
    setFeatureSaving(true);
    try {
      await apiService.adminUpdateUserFeatures(featureUser.id, featureEdits);
      setUsers(prev => prev.map(u => u.id === featureUser.id ? { ...u, enabled_features: featureEdits } : u));
      setFeatureUser(null);
      setImportMsg(`Features updated for user '${featureUser.username}'.`);
      setTimeout(() => setImportMsg(null), 3000);
    } catch (err: any) {
      setImportErr(err?.response?.data?.detail || 'Failed to update features.');
    } finally {
      setFeatureSaving(false);
    }
  };

  const handleImportUsers = async () => {
    setImporting(true);
    setImportMsg(null);
    setImportErr(null);
    try {
      const result = await apiService.importNavidromeUsers();
      setImportMsg(`Imported ${result.count} users: ${result.imported.join(', ')}`);
      const data = await apiService.getUsers();
      setUsers(data.users);
    } catch (err: any) {
      setImportErr(err?.response?.data?.detail || 'Failed to import users.');
    } finally {
      setImporting(false);
    }
  };

  // Dirty flag: watch all form fields for changes after initial load
  const isFirstRender = useRef(true);
  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    setIsDirty(
      slskdUrl !== (configResponse?.parsed?.slskd?.base_url || 'http://slskd:5030') ||
      slskdApiKey !== (configResponse?.parsed?.slskd?.api_key || '') ||
      slskdDownloadsDir !== (configResponse?.parsed?.slskd?.downloads_dir || '/slskd_downloads') ||
      audioQualityPreset !== (configResponse?.parsed?.slskd?.audio_quality?.preset || 'lossless') ||
      provider !== (configResponse?.parsed?.lyrics?.provider || 'lrclib') ||
      lyricsUrl !== (configResponse?.parsed?.lyrics?.base_url || 'https://lrclib.net') ||
      dailyTime !== (configResponse?.parsed?.schedule?.daily_time || '04:00') ||
      weeklyTime !== (configResponse?.parsed?.schedule?.weekly_time || '04:00') ||
      weeklyDay !== (configResponse?.parsed?.schedule?.weekly_day || 'tue') ||
      fileChecksTime !== (configResponse?.parsed?.schedule?.file_checks_time || '04:00') ||
      fileChecksDay !== (configResponse?.parsed?.schedule?.file_checks_day || 'sun') ||
      runOnStartup !== (configResponse?.parsed?.schedule?.run_on_startup !== false) ||
      String(batchSize) !== String(configResponse?.parsed?.schedule?.batch_size || 5) ||
      String(maxAttempts) !== String(configResponse?.parsed?.schedule?.max_candidate_attempts || 3) ||
      weeklyOutputDir !== (configResponse?.parsed?.paths?.weekly_output_dir || '/data/weekly/current') ||
      navidromeUrl !== (configResponse?.parsed?.navidrome?.url || '') ||
      navidromeUsername !== (configResponse?.parsed?.navidrome?.username || '') ||
      navidromePassword !== (configResponse?.parsed?.navidrome?.password || '') ||
      acoustidApiKey !== (configResponse?.parsed?.acoustid?.api_key || '') ||
      String(httpTimeout) !== String(configResponse?.parsed?.timeouts?.http_seconds || 20) ||
      String(searchTimeout) !== String(configResponse?.parsed?.timeouts?.search_seconds || 30) ||
      String(downloadTimeout) !== String(configResponse?.parsed?.timeouts?.download_seconds || 240) ||
      logLevel !== (configResponse?.parsed?.log_level || 'INFO')
    );
  }, [slskdUrl, slskdApiKey, slskdDownloadsDir, audioQualityPreset, audioQualityLadder, provider, lyricsUrl, dailyTime, weeklyTime, weeklyDay, fileChecksTime, fileChecksDay, runOnStartup, batchSize, maxAttempts, weeklyOutputDir, navidromeUrl, navidromeUsername, navidromePassword, acoustidApiKey, httpTimeout, searchTimeout, downloadTimeout, logLevel, configResponse]);

  // Warn on page unload if there are unsaved changes
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [isDirty]);

  const handleTabChange = (_event: React.SyntheticEvent, newValue: number) => {
    setTabValue(newValue);
    setSuccessMsg(null);
    setErrorMsg(null);
  };

  const handleTestLB = async () => {
    setLbTest({ status: 'testing', message: '' });
    try {
      const res = await apiService.testListenBrainz();
      setLbTest({ status: 'ok', message: res.message });
    } catch (e: any) {
      setLbTest({ status: 'error', message: e.response?.data?.detail || 'Connection failed' });
    }
  };

  const handleTestSlskd = async () => {
    setSlskdTest({ status: 'testing', message: '' });
    try {
      const res = await apiService.testSlskd();
      setSlskdTest({ status: 'ok', message: res.message });
    } catch (e: any) {
      setSlskdTest({ status: 'error', message: e.response?.data?.detail || 'Connection failed' });
    }
  };
 
  const handleTestNavidrome = async () => {
    setNdTest({ status: 'testing', message: '' });
    try {
      const res = await apiService.testNavidrome();
      setNdTest({ status: 'ok', message: res.message });
    } catch (e: any) {
      setNdTest({ status: 'error', message: e.response?.data?.detail || 'Connection failed' });
    }
  };

  const handleSaveGUI = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSuccessMsg(null);
    setErrorMsg(null);

    const payload = {
      // Preserve existing listenbrainz config (managed per-user in My Settings)
      listenbrainz: configResponse?.parsed?.listenbrainz,
      slskd: {
        base_url: slskdUrl,
        api_key: slskdApiKey,
        downloads_dir: slskdDownloadsDir,
        audio_quality: {
          preset: audioQualityPreset,
          custom_profiles: audioQualityLadder
            .filter((x: StandardQuality) => x.checked)
            .map((x: StandardQuality) => ({
              format: x.format,
              min_bitrate: x.min_bitrate,
              max_bitrate: x.max_bitrate,
              bit_depth: x.bit_depth,
              sample_rate: x.sample_rate,
            })),
        }
      },
      navidrome: {
        url: navidromeUrl,
        username: navidromeUsername,
        password: navidromePassword,
      },
      acoustid: {
        api_key: acoustidApiKey,
      },
      lyrics: {
        provider: provider,
        base_url: lyricsUrl,
      },
      schedule: {
        daily_time: dailyTime,
        weekly_time: weeklyTime,
        weekly_day: weeklyDay,
        file_checks_time: fileChecksTime,
        file_checks_day: fileChecksDay,
        run_on_startup: runOnStartup,
        batch_size: Number(batchSize),
        max_candidate_attempts: Number(maxAttempts),
      },
      paths: {
        weekly_output_dir: weeklyOutputDir,
        navidrome_playlists_dir: configResponse?.parsed?.paths?.navidrome_playlists_dir || '/navidrome_playlists',
        music_dir: configResponse?.parsed?.paths?.music_dir || '/music',
      },
      timeouts: {
        http_seconds: Number(httpTimeout),
        search_seconds: Number(searchTimeout),
        download_seconds: Number(downloadTimeout),
      },
      log_level: logLevel,
    };

    try {
      await apiService.updateConfig(payload);
      setSuccessMsg("Configuration saved and hot-reloaded successfully!");
      setIsDirty(false);
      // Reload values from file to update raw text tab
      const data = await apiService.getConfig();
      setConfigResponse(data);
      setRawYaml(data.raw_yaml);
    } catch (e: any) {
      const detail: string = e.response?.data?.detail || "Failed to update configuration.";
      if (detail.includes('Permission denied') || detail.includes('Errno 13')) {
        setErrorMsg(`Permission denied writing config.yml — run on Docker host:\n  chown 10001:10001 /path/to/config.yml\nthen restart the container.`);
      } else {
        setErrorMsg(detail);
      }
    } finally {
      setSaving(false);
    }
  };


  const executeSaveRaw = async () => {
    setSaving(true);
    setSuccessMsg(null);
    setErrorMsg(null);
    try {
      await apiService.updateConfig({ raw_yaml: rawYaml });
      setSuccessMsg("RAW Configuration saved and hot-reloaded successfully!");
      // Reload values
      await fetchConfig();
    } catch (e: any) {
      setErrorMsg(e.response?.data?.detail || "Failed to update raw configuration.");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <Box display="flex" justifyContent="center" alignItems="center" minHeight="50vh">
        <CircularProgress size={50} />
      </Box>
    );
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 800 }}>Configuration</Typography>
        <Typography variant="body2" color="text.secondary">
          Settings are saved live and applied instantly without docker container restarts.
        </Typography>
      </Box>

      {/* Admin: Manage Users */}
      {user?.isAdmin && (
        <Card sx={{ borderRadius: 4 }}>
          <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
            <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 2, mb: 3 }}>
              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                  <AdminIcon color="primary" />
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>Manage Users</Typography>
                </Box>
                <Typography variant="body2" color="text.secondary">
                  Import Navidrome users so everyone on your server can sign in
                </Typography>
              </Box>
              <Button
                variant="outlined"
                startIcon={importing ? <CircularProgress size={18} color="inherit" /> : <ImportIcon />}
                onClick={handleImportUsers}
                disabled={importing}
                sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
              >
                {importing ? 'Importing…' : 'Import from Navidrome'}
              </Button>
            </Box>
            {importMsg && <Alert severity="success" onClose={() => setImportMsg(null)} sx={{ mb: 2 }}>{importMsg}</Alert>}
            {importErr && <Alert severity="error" onClose={() => setImportErr(null)} sx={{ mb: 2 }}>{importErr}</Alert>}
            <Divider sx={{ mb: 3 }} />
            {usersLoading ? (
              <Box display="flex" justifyContent="center" p={3}><CircularProgress /></Box>
            ) : users.length === 0 ? (
              <Typography color="text.secondary">
                No users yet. Click "Import from Navidrome" to pull all server users.
              </Typography>
            ) : (
              <TableContainer component={Paper} elevation={0}
                sx={{ background: 'transparent', border: '1px solid', borderColor: 'divider', borderRadius: 2, overflowX: 'auto' }}>
                <Table size="small" sx={{ minWidth: 600 }}>
                  <TableHead>
                    <TableRow>
                      <TableCell sx={{ fontWeight: 700 }}>Username</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Role</TableCell>
                      <TableCell sx={{ fontWeight: 700, minWidth: 220 }}>Music Library Directory</TableCell>
                      <TableCell sx={{ fontWeight: 700, minWidth: 220 }}>Playlists Directory</TableCell>
                      <TableCell sx={{ fontWeight: 700 }}>Last Login</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {users.map(u => {
                      const edit = userEdits?.[u.id];
                      return (
                        <TableRow key={u.id} hover>
                          <TableCell>
                            <Typography variant="body2" sx={{ fontWeight: 600 }}>{u.username}</Typography>
                            <Typography variant="caption" color="text.secondary">{u.display_name || ''}</Typography>
                          </TableCell>
                          <TableCell>
                            {u.is_admin ? (
                              <Chip label="Admin" size="small" color="primary" sx={{ fontWeight: 600 }} />
                            ) : (
                              <Chip label="User" size="small" sx={{ fontWeight: 600 }} />
                            )}
                          </TableCell>
                          <TableCell>
                            <TextField
                              size="small"
                              fullWidth
                              placeholder="/music/username"
                              value={edit?.music_dir ?? u.music_dir ?? ''}
                              onChange={e => setUserEdits(prev => ({ ...prev!, [u.id]: { ...prev![u.id], music_dir: e.target.value } }))}
                              sx={{ '& .MuiOutlinedInput-root': { borderRadius: 1.5 } }}
                            />
                          </TableCell>
                          <TableCell>
                            <TextField
                              size="small"
                              fullWidth
                              placeholder="/music/username/Playlists"
                              value={edit?.playlist_dir ?? u.playlist_dir ?? ''}
                              onChange={e => setUserEdits(prev => ({ ...prev!, [u.id]: { ...prev![u.id], playlist_dir: e.target.value } }))}
                              sx={{ '& .MuiOutlinedInput-root': { borderRadius: 1.5 } }}
                            />
                          </TableCell>
                          <TableCell>
                            <Typography variant="caption" color="text.secondary">
                              {u.last_login ? new Date(u.last_login).toLocaleDateString() : 'Never'}
                            </Typography>
                          </TableCell>
                          <TableCell>
                            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                              <Tooltip title="Configure Features">
                                <IconButton
                                  size="small"
                                  onClick={() => handleOpenFeatures(u)}
                                  sx={{ color: 'text.secondary' }}
                                >
                                  <TuneIcon fontSize="small" />
                                </IconButton>
                              </Tooltip>
                              <Button
                                size="small"
                                variant="contained"
                                disabled={edit?.saving}
                                onClick={() => handleSaveUserPaths(u.id)}
                                sx={{ borderRadius: 1.5, textTransform: 'none', fontWeight: 600, whiteSpace: 'nowrap' }}
                              >
                                {edit?.saving ? <CircularProgress size={14} color="inherit" /> : 'Save'}
                              </Button>
                            </Box>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </TableContainer>
            )}
          </CardContent>
        </Card>
      )}

      {/* Admin: Features configuration dialog */}
      <Dialog
        open={Boolean(featureUser)}
        onClose={() => setFeatureUser(null)}
        maxWidth="xs"
        fullWidth
        PaperProps={{ sx: { borderRadius: 4 } }}
      >
        <DialogTitle sx={{ fontWeight: 800 }}>
          Features for @{featureUser?.username}
        </DialogTitle>
        <DialogContent>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Enable or disable background services and automatic downloads for this user.
          </Typography>
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={featureEdits.starred_sync ?? true}
                  onChange={e => setFeatureEdits(prev => ({ ...prev, starred_sync: e.target.checked }))}
                />
              }
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>⭐ Starred Tracks Sync</Typography>
                  <Typography variant="caption" color="text.secondary">Sync starred tracks from Navidrome every 5min</Typography>
                </Box>
              }
            />
            <FormControlLabel
              control={
                <Switch
                  checked={featureEdits.listenbrainz_sync ?? true}
                  onChange={e => setFeatureEdits(prev => ({ ...prev, listenbrainz_sync: e.target.checked }))}
                />
              }
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>🎵 ListenBrainz Sync</Typography>
                  <Typography variant="caption" color="text.secondary">Pull Exploration / Jams playlists from ListenBrainz</Typography>
                </Box>
              }
            />
            <FormControlLabel
              control={
                <Switch
                  checked={featureEdits.discovery ?? true}
                  onChange={e => setFeatureEdits(prev => ({ ...prev, discovery: e.target.checked }))}
                />
              }
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>🔍 Discovery Flow</Typography>
                  <Typography variant="caption" color="text.secondary">Save new tracks into the Explore/discovery folder</Typography>
                </Box>
              }
            />
            <FormControlLabel
              control={
                <Switch
                  checked={featureEdits.album_downloads ?? true}
                  onChange={e => setFeatureEdits(prev => ({ ...prev, album_downloads: e.target.checked }))}
                />
              }
              label={
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>📥 Automatic Album Downloads</Typography>
                  <Typography variant="caption" color="text.secondary">Download full albums for starred tracks automatically</Typography>
                </Box>
              }
            />
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 3, pt: 1 }}>
          <Button
            onClick={() => setFeatureUser(null)}
            variant="text"
            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600 }}
          >
            Cancel
          </Button>
          <Button
            onClick={handleSaveFeatures}
            variant="contained"
            disabled={featureSaving}
            sx={{ borderRadius: 2, textTransform: 'none', fontWeight: 600, px: 3 }}
          >
            {featureSaving ? <CircularProgress size={18} color="inherit" /> : 'Save Changes'}
          </Button>
        </DialogActions>
      </Dialog>

      {successMsg && <Alert severity="success" onClose={() => setSuccessMsg(null)}>{successMsg}</Alert>}
      {errorMsg && <Alert severity="error" onClose={() => setErrorMsg(null)}>{errorMsg}</Alert>}
      {configResponse?.validation_errors && (
        <Alert severity="warning" sx={{ borderRadius: 3 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>Active configuration warning:</Typography>
          <pre style={{ margin: '8px 0 0 0', whiteSpace: 'pre-wrap', fontFamily: 'JetBrains Mono', fontSize: '0.8rem' }}>
            {configResponse.validation_errors}
          </pre>
        </Alert>
      )}

      <Card>
        <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
          <Tabs value={tabValue} onChange={handleTabChange} aria-label="settings edit modes" variant="scrollable" scrollButtons="auto">
            <Tab icon={<SettingsIcon />} iconPosition="start" label="GUI Editor" />
            <Tab icon={<EditIcon />} iconPosition="start" label="YAML Raw Editor" />
          </Tabs>
        </Box>
        
        <CardContent sx={{ p: { xs: 2, sm: 4 } }}>
          {tabValue === 0 && (
            <form onSubmit={handleSaveGUI}>
              <Grid container spacing={{ xs: 2, md: 4 }}>
                {/* slskd Section */}
                <Grid item xs={12}>
                  <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 2, mb: 1 }}>
                    <Typography variant="h6" sx={{ fontWeight: 700, color: theme.palette.primary.main }}>
                      slskd (Soulseek Daemon)
                    </Typography>
                    <Button
                      id="btn-test-slskd"
                      size="small"
                      variant="outlined"
                      startIcon={slskdTest.status === 'testing' ? <CircularProgress size={14} /> : <TestIcon />}
                      onClick={handleTestSlskd}
                      disabled={slskdTest.status === 'testing'}
                    >
                      Test Connection
                    </Button>
                    {slskdTest.status === 'ok' && (
                      <Chip icon={<CheckCircleIcon />} label={slskdTest.message} color="success" size="small" />
                    )}
                    {slskdTest.status === 'error' && (
                      <Chip icon={<ErrorIcon />} label={slskdTest.message} color="error" size="small" sx={{ maxWidth: 360, '& .MuiChip-label': { whiteSpace: 'normal' } }} />
                    )}
                  </Box>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={{ xs: 2, sm: 3 }}>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        label="slskd Base URL"
                        value={slskdUrl}
                        onChange={(e) => setSlskdUrl(e.target.value)}
                        placeholder="e.g. http://localhost:5030"
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <TextField
                        fullWidth
                        label="slskd API Key / Token"
                        value={slskdApiKey}
                        onChange={(e) => setSlskdApiKey(e.target.value)}
                        type="password"
                        placeholder="Enter slskd API key if active"
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        label="slskd Downloads Volume Directory"
                        value={slskdDownloadsDir}
                        onChange={(e) => setSlskdDownloadsDir(e.target.value)}
                        helperText="Staging scans this directory inside the docker container"
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <FormControl fullWidth required>
                        <InputLabel>Audio Quality Preset</InputLabel>
                        <Select
                          value={audioQualityPreset}
                          label="Audio Quality Preset"
                          onChange={(e) => setAudioQualityPreset(e.target.value)}
                        >
                          <MenuItem value="lossless">Lossless (FLAC, WAV, ALAC)</MenuItem>
                          <MenuItem value="storage_saver">Storage Saver (MP3 192-320)</MenuItem>
                          <MenuItem value="custom">Custom</MenuItem>
                        </Select>
                      </FormControl>
                    </Grid>
                    {audioQualityPreset === 'custom' && (
                      <Grid item xs={12}>
                        <Typography variant="subtitle2" mb={1} fontWeight={700}>Custom Quality Ladder</Typography>
                        <Typography variant="caption" color="text.secondary" display="block" mb={2}>
                          Qualities higher in the list are more preferred. Qualities within the same group are equal. Only checked qualities are wanted. Drag and drop items to adjust preference priority.
                        </Typography>

                        <Box sx={{ mb: 3, display: 'flex', gap: 2 }}>
                          <Button size="small" variant="outlined" onClick={handleResetToLossless}>Reset to Lossless Defaults</Button>
                          <Button size="small" variant="outlined" onClick={handleResetToStorageSaver}>Reset to Storage Saver Defaults</Button>
                        </Box>

                        <Paper variant="outlined" sx={{ bgcolor: '#0d0c12', borderColor: '#1b1a23', borderRadius: 3, p: 1, mb: 3 }}>
                          <List sx={{ p: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
                            {audioQualityLadder.map((item, idx) => {
                              const isDragged = draggedIndex === idx;
                              return (
                                <ListItem
                                  key={item.id}
                                  draggable
                                  onDragStart={() => handleDragStart(idx)}
                                  onDragOver={(e) => handleDragOver(e, idx)}
                                  onDragEnd={handleDragEnd}
                                  sx={{
                                    border: '1px solid',
                                    borderColor: item.checked ? 'primary.main' : '#22212d',
                                    borderRadius: 2,
                                    bgcolor: isDragged ? 'rgba(144, 202, 249, 0.08)' : (item.checked ? '#14121d' : '#0c0b11'),
                                    opacity: isDragged ? 0.5 : 1,
                                    cursor: 'grab',
                                    transition: 'all 0.15s ease',
                                    '&:hover': {
                                      borderColor: 'primary.light',
                                      bgcolor: '#191724',
                                    },
                                    py: 1,
                                    px: 2,
                                    display: 'flex',
                                    alignItems: 'center',
                                    userSelect: 'none'
                                  }}
                                >
                                  <Box sx={{ display: 'flex', alignItems: 'center', mr: 2, color: 'text.secondary' }}>
                                    <DragIcon />
                                  </Box>
                                  <Checkbox
                                    checked={item.checked}
                                    onChange={() => handleToggleChecked(item.id)}
                                    color="primary"
                                    size="small"
                                  />
                                  <ListItemText
                                    primary={item.name}
                                    secondary={
                                      item.checked 
                                        ? `Priority #${idx + 1} preferred` 
                                        : 'Not wanted (ignored during search matching)'
                                    }
                                    primaryTypographyProps={{ fontWeight: item.checked ? 700 : 500, color: item.checked ? 'text.primary' : 'text.secondary', fontSize: '0.9rem' }}
                                    secondaryTypographyProps={{ fontSize: '0.75rem', color: item.checked ? 'primary.light' : 'text.disabled' }}
                                  />
                                  {item.checked && (
                                    <Chip 
                                      label={`Priority #${idx + 1}`} 
                                      size="small" 
                                      color="primary" 
                                      variant="outlined" 
                                      sx={{ fontWeight: 600, fontSize: '0.7rem' }} 
                                    />
                                  )}
                                </ListItem>
                              );
                            })}
                          </List>
                        </Paper>

                        {/* Inline Form to add a new custom quality to the ladder */}
                        <Box sx={{ p: 2.5, bgcolor: '#0f0e15', border: '1px dashed #282736', borderRadius: 3 }}>
                          <Typography variant="body2" fontWeight={700} sx={{ mb: 2 }}>Add Custom Format to Ladder</Typography>
                          <Grid container spacing={{ xs: 1.5, sm: 2 }} alignItems="center">
                            <Grid item xs={12} sm={3}>
                              <FormControl fullWidth size="small">
                                <InputLabel>Format</InputLabel>
                                <Select
                                  value={customFormat}
                                  label="Format"
                                  onChange={(e) => setCustomFormat(e.target.value)}
                                >
                                  {['flac', 'mp3', 'wav', 'alac', 'ape', 'm4a', 'ogg', 'opus', 'wv'].map(f => (
                                    <MenuItem key={f} value={f}>{f.toUpperCase()}</MenuItem>
                                  ))}
                                </Select>
                              </FormControl>
                            </Grid>
                            <Grid item xs={12} sm={2.5}>
                              <TextField
                                fullWidth
                                size="small"
                                label="Min Bitrate (kbps)"
                                type="number"
                                value={customMinBitrate || ''}
                                onChange={(e) => setCustomMinBitrate(Number(e.target.value))}
                                placeholder="0 = any"
                              />
                            </Grid>
                            <Grid item xs={12} sm={2}>
                              <TextField
                                fullWidth
                                size="small"
                                label="Bit Depth"
                                type="number"
                                value={customBitDepth || ''}
                                onChange={(e) => setCustomBitDepth(Number(e.target.value))}
                                placeholder="0 = any"
                              />
                            </Grid>
                            <Grid item xs={12} sm={2.5}>
                              <TextField
                                fullWidth
                                size="small"
                                label="Sample Rate (Hz)"
                                type="number"
                                value={customSampleRate || ''}
                                onChange={(e) => setCustomSampleRate(Number(e.target.value))}
                                placeholder="0 = any"
                              />
                            </Grid>
                            <Grid item xs={12} sm={2}>
                              <Button
                                fullWidth
                                variant="contained"
                                startIcon={<AddIcon />}
                                onClick={handleAddCustomQuality}
                              >
                                Add
                              </Button>
                            </Grid>
                          </Grid>
                        </Box>
                      </Grid>
                    )}
                  </Grid>
                </Grid>

                {/* Navidrome Section */}
                <Grid item xs={12}>
                  <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 2, mb: 1 }}>
                    <Typography variant="h6" sx={{ fontWeight: 700, color: theme.palette.primary.main }}>
                      Navidrome Integration (Subsonic API)
                    </Typography>
                    <Button
                      id="btn-test-navidrome"
                      size="small"
                      variant="outlined"
                      startIcon={ndTest.status === 'testing' ? <CircularProgress size={14} /> : <TestIcon />}
                      onClick={handleTestNavidrome}
                      disabled={ndTest.status === 'testing'}
                    >
                      Test Connection
                    </Button>
                    {ndTest.status === 'ok' && (
                      <Chip icon={<CheckCircleIcon />} label={ndTest.message} color="success" size="small" />
                    )}
                    {ndTest.status === 'error' && (
                      <Chip icon={<ErrorIcon />} label={ndTest.message} color="error" size="small" sx={{ maxWidth: 360, '& .MuiChip-label': { whiteSpace: 'normal' } }} />
                    )}
                  </Box>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={{ xs: 2, sm: 3 }}>
                    <Grid item xs={12} md={6}>
                      <TextField
                        fullWidth
                        label="Navidrome Base URL"
                        value={navidromeUrl}
                        onChange={(e) => setNavidromeUrl(e.target.value)}
                        placeholder="e.g. http://192.168.1.3:4533"
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <TextField
                        fullWidth
                        label="Subsonic Username"
                        value={navidromeUsername}
                        onChange={(e) => setNavidromeUsername(e.target.value)}
                        placeholder="Enter Subsonic/Navidrome username"
                      />
                    </Grid>
                    <Grid item xs={12}>
                      <TextField
                        fullWidth
                        label="Subsonic Password / Token"
                        value={navidromePassword}
                        onChange={(e) => setNavidromePassword(e.target.value)}
                        type="password"
                        placeholder="Enter password or Subsonic token"
                      />
                    </Grid>
                  </Grid>
                </Grid>
 
                {/* Lyrics Section */}
                <Grid item xs={12}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 1, color: theme.palette.primary.main }}>
                    Lyrics Lookups
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={{ xs: 2, sm: 3 }}>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        label="Provider"
                        value={provider}
                        disabled
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        label="LRCLIB API Base URL"
                        value={lyricsUrl}
                        onChange={(e) => setLyricsUrl(e.target.value)}
                      />
                    </Grid>
                  </Grid>
                </Grid>

                {/* AcoustID Section */}
                <Grid item xs={12}>
                  <Box sx={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 2, mb: 1 }}>
                    <Typography variant="h6" sx={{ fontWeight: 700, color: theme.palette.primary.main }}>
                      AcoustID Verification
                    </Typography>
                  </Box>
                  <Divider sx={{ mb: 2 }} />
                  <TextField
                    label="AcoustID API Key"
                    variant="filled"
                    fullWidth
                    value={acoustidApiKey}
                    onChange={(e) => setAcoustidApiKey(e.target.value)}
                    helperText="Optional but highly recommended. Get a free API key at acoustid.org to fingerprint tracks and ensure downloaded songs match exactly."
                    sx={{ mb: 2 }}
                  />
                </Grid>

                {/* Scheduler Section */}
                <Grid item xs={12}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 1, color: theme.palette.primary.main }}>
                    Scheduling & Execution Limits
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={{ xs: 2, sm: 3 }}>
                    <Grid item xs={12} md={4}>
                      <TextField
                        required
                        fullWidth
                        label="Daily Sync Time"
                        type="time"
                        value={dailyTime}
                        onChange={(e) => setDailyTime(e.target.value)}
                        InputLabelProps={{ shrink: true }}
                        inputProps={{ step: 300 }}
                      />
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <TextField
                        required
                        fullWidth
                        label="Weekly Sync Time"
                        type="time"
                        value={weeklyTime}
                        onChange={(e) => setWeeklyTime(e.target.value)}
                        InputLabelProps={{ shrink: true }}
                        inputProps={{ step: 300 }}
                      />
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <FormControl fullWidth required>
                        <InputLabel id="weekly-day-label">Weekly Sync Day</InputLabel>
                        <Select
                          labelId="weekly-day-label"
                          value={weeklyDay}
                          label="Weekly Sync Day"
                          onChange={(e) => setWeeklyDay(e.target.value as string)}
                        >
                          <MenuItem value="mon">Monday</MenuItem>
                          <MenuItem value="tue">Tuesday</MenuItem>
                          <MenuItem value="wed">Wednesday</MenuItem>
                          <MenuItem value="thu">Thursday</MenuItem>
                          <MenuItem value="fri">Friday</MenuItem>
                          <MenuItem value="sat">Saturday</MenuItem>
                          <MenuItem value="sun">Sunday</MenuItem>
                        </Select>
                      </FormControl>
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <TextField
                        required
                        fullWidth
                        type="time"
                        label="Automated File Checks Time"
                        value={fileChecksTime}
                        onChange={(e) => setFileChecksTime(e.target.value)}
                        InputLabelProps={{ shrink: true }}
                        inputProps={{ step: 300 }}
                      />
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <FormControl fullWidth required>
                        <InputLabel id="filechecks-day-label">Automated File Checks Day</InputLabel>
                        <Select
                          labelId="filechecks-day-label"
                          value={fileChecksDay}
                          label="Automated File Checks Day"
                          onChange={(e) => setFileChecksDay(e.target.value as string)}
                        >
                          <MenuItem value="mon">Monday</MenuItem>
                          <MenuItem value="tue">Tuesday</MenuItem>
                          <MenuItem value="wed">Wednesday</MenuItem>
                          <MenuItem value="thu">Thursday</MenuItem>
                          <MenuItem value="fri">Friday</MenuItem>
                          <MenuItem value="sat">Saturday</MenuItem>
                          <MenuItem value="sun">Sunday</MenuItem>
                        </Select>
                      </FormControl>
                    </Grid>
                    <Grid item xs={12} md={6} display="flex" alignItems="center">
                      <FormControlLabel
                        control={
                          <Switch
                            checked={runOnStartup}
                            onChange={(e) => setRunOnStartup(e.target.checked)}
                            color="primary"
                          />
                        }
                        label="Run synchronization immediately on startup"
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        type="number"
                        label="Batch Sync Concurrency (Concurrent transfers)"
                        value={batchSize}
                        onChange={(e) => setBatchSize(Number(e.target.value))}
                      />
                    </Grid>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        type="number"
                        label="Max candidate search attempts per track"
                        value={maxAttempts}
                        onChange={(e) => setMaxAttempts(Number(e.target.value))}
                      />
                    </Grid>
                  </Grid>
                </Grid>

                {/* Paths and Timeouts */}
                <Grid item xs={12}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 1, color: theme.palette.primary.main }}>
                    Storage Paths & Request Timeouts
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={{ xs: 2, sm: 3 }}>
                    <Grid item xs={12} md={6}>
                      <TextField
                        required
                        fullWidth
                        label="Staged Output Target Directory"
                        value={weeklyOutputDir}
                        onChange={(e) => setWeeklyOutputDir(e.target.value)}
                        helperText="Atomic switch targets this folder"
                      />
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <TextField
                        required
                        fullWidth
                        type="number"
                        label="HTTP Request Timeout (seconds)"
                        value={httpTimeout}
                        onChange={(e) => setHttpTimeout(Number(e.target.value))}
                      />
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <TextField
                        required
                        fullWidth
                        type="number"
                        label="Search Complete Max Wait (seconds)"
                        value={searchTimeout}
                        onChange={(e) => setSearchTimeout(Number(e.target.value))}
                      />
                    </Grid>
                    <Grid item xs={12} md={4}>
                      <TextField
                        required
                        fullWidth
                        type="number"
                        label="Track Download Max Wait (seconds)"
                        value={downloadTimeout}
                        onChange={(e) => setDownloadTimeout(Number(e.target.value))}
                      />
                    </Grid>
                  </Grid>
                </Grid>

                {/* Advanced Logging */}
                <Grid item xs={12}>
                  <Typography variant="h6" sx={{ fontWeight: 700, mb: 1, color: theme.palette.primary.main }}>
                    System Diagnostics
                  </Typography>
                  <Divider sx={{ mb: 2 }} />
                  <Grid container spacing={{ xs: 2, sm: 3 }}>
                    <Grid item xs={12} md={6}>
                      <FormControl fullWidth>
                        <InputLabel>Console/File Log Level</InputLabel>
                        <Select
                          value={logLevel}
                          label="Console/File Log Level"
                          onChange={(e) => setLogLevel(e.target.value)}
                        >
                          <MenuItem value="DEBUG">DEBUG (Verbose)</MenuItem>
                          <MenuItem value="INFO">INFO (Normal)</MenuItem>
                          <MenuItem value="WARNING">WARNING (Issues only)</MenuItem>
                          <MenuItem value="ERROR">ERROR (Severe errors)</MenuItem>
                        </Select>
                      </FormControl>
                    </Grid>
                  </Grid>
                </Grid>

                <Grid item xs={12}>
                  <Box display="flex" alignItems="center">
                    <Button
                      type="submit"
                      variant="contained"
                      size="large"
                      color="primary"
                      startIcon={saving ? <CircularProgress size={20} color="inherit" /> : <SaveIcon />}
                      disabled={saving}
                      sx={{ px: 5, py: 1.5 }}
                    >
                      Save Changes
                    </Button>
                    {isDirty && (
                      <Chip
                        label="Unsaved changes"
                        color="warning"
                        size="small"
                        sx={{ ml: 2, fontWeight: 600 }}
                      />
                    )}
                  </Box>
                </Grid>
              </Grid>
            </form>
          )}

          {tabValue === 1 && (
            <Box display="flex" flexDirection="column" gap={2}>
              <Typography variant="body2" color="text.secondary">
                Directly edit the `config.yml` settings file. Ensure indentation is correct YAML standard syntax.
              </Typography>
              <TextField
                multiline
                fullWidth
                rows={20}
                variant="outlined"
                value={rawYaml}
                onChange={(e) => setRawYaml(e.target.value)}
                inputProps={{
                  style: { 
                    fontFamily: 'JetBrains Mono, Courier New, monospace',
                    fontSize: '0.9rem',
                    lineHeight: '1.4'
                  }
                }}
              />
              <Box>
                <Button
                  variant="contained"
                  color="primary"
                  size="large"
                  startIcon={saving ? <CircularProgress size={20} color="inherit" /> : <SaveIcon />}
                  disabled={saving}
                  onClick={executeSaveRaw}
                  sx={{ px: 5, py: 1.5 }}
                >
                  Save YAML Code
                </Button>
              </Box>
            </Box>
          )}
        </CardContent>
      </Card>
    </Box>
  );
};
export default Configuration;
