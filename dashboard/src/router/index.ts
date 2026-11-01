import { createRouter, createWebHashHistory } from 'vue-router';
import MainRoutes from './MainRoutes';
import AuthRoutes from './AuthRoutes';
import ChatBoxRoutes from './ChatBoxRoutes';
import { useAuthStore } from '@/stores/auth';
import { useRouterLoadingStore } from '@/stores/routerLoading';

export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    MainRoutes,
    AuthRoutes,
    ChatBoxRoutes
  ]
});

interface AuthStore {
  username: string;
  role: string;
  refreshIdentity(): Promise<void>;
  returnUrl: string | null;
  login(
    username: string,
    password: string,
    code?: string,
    trustDeviceToken?: boolean,
  ): Promise<void | 'totp_required' | 'upgrade_recovery_required'>;
  logout(): void;
  has_token(): boolean;
}

router.beforeEach(async (to, from, next) => {
  if (from.name && from.path !== to.path) {
    const loadingStore = useRouterLoadingStore();
    loadingStore.start();
  }

  const publicPages = ['/auth/login', '/auth/setup'];
  const authRequired = !publicPages.includes(to.path);
  const auth: AuthStore = useAuthStore();

  if (auth.has_token() && to.path !== '/auth/login') {
    try { await auth.refreshIdentity(); } catch { return next('/auth/login'); }
    if (auth.role === 'user' && (!/^\/chat(?:box)?(?:\/|$)/.test(to.path) || /^\/chat(?:box)?\/models(?:\/|$)/.test(to.path))) return next('/chat');
  }

  // Redirect authenticated callers to their role's entry point.
  if (to.path === '/auth/login' && auth.has_token()) {
    if (auth.role === 'admin' && sessionStorage.getItem('astrbot-full-restore')) return next('/settings#settings-maintenance');
    return next(auth.role === 'user' ? '/chat' : '/welcome');
  }

  if (to.matched.some((record) => record.meta.requiresAuth)) {
    if (authRequired && !auth.has_token()) {
      auth.returnUrl = to.fullPath;
      return next('/auth/login');
    }
    return next();
  } else {
    next();
  }
});

router.afterEach(() => {
  const loadingStore = useRouterLoadingStore();
  loadingStore.finish();
});
