import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { apiService, AuthUser } from '../api';

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  isConfigured: boolean;
  setIsConfigured: (val: boolean) => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue>({
  user: null,
  loading: true,
  isConfigured: true,
  setIsConfigured: () => {},
  login: async () => {},
  logout: async () => {},
  refreshUser: async () => {},
});

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isConfigured, setIsConfigured] = useState(true);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    try {
      // 1. Check if app is configured (public endpoint — always available)
      const status = await apiService.getStatus();
      setIsConfigured(status.is_configured);

      if (!status.is_configured) {
        setUser(null);
        return;
      }

      // 2. Try to rehydrate the session cookie (may return 401 if not logged in)
      try {
        const me = await apiService.me();
        setUser(me);
      } catch {
        // 401 = not logged in yet, but app IS configured — show Login page
        setUser(null);
      }
    } catch (err) {
      // /api/status itself failed (network error, server down)
      // Keep isConfigured as-is so we don't flash the setup page on flaky network
      setUser(null);
    }
  }, []);

  // On mount: try to rehydrate session and check config status
  useEffect(() => {
    refreshUser().finally(() => setLoading(false));
  }, [refreshUser]);

  const login = useCallback(async (username: string, password: string) => {
    const result = await apiService.login(username, password);
    setUser(result.user);
    setIsConfigured(true);
  }, []);

  const logout = useCallback(async () => {
    try {
      await apiService.logout();
    } finally {
      setUser(null);
      window.location.href = '/login';
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, isConfigured, setIsConfigured, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
export default AuthContext;
