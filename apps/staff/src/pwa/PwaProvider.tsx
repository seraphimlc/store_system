import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { Dialog, List, Toast } from 'antd-mobile';
import { activateUpdate, iosDevice, standalone, type InstallPrompt } from './client';

type State = { install: InstallPrompt | null; installed: boolean; registration: ServiceWorkerRegistration | null; waiting: ServiceWorker | null; error: boolean };
const initial: State = { install: null, installed: false, registration: null, waiting: null, error: false };
const PwaContext = createContext(initial);

export function PwaProvider({ children, language }: { children: ReactNode; language: string }) {
  const [state, setState] = useState(initial);
  useEffect(() => {
    let live = true;
    const cleanups: (() => void)[] = [];
    const patch = (next: Partial<State>) => { if (live) setState(old => ({ ...old, ...next })); };
    const installed = () => patch({ installed: standalone(), install: null });
    const prompt = (event: Event) => {
      event.preventDefault();
      const install = event as InstallPrompt;
      patch({ install });
      void install.userChoice.then(() => patch({ install: null })).catch(() => patch({ install: null }));
    };
    const media = matchMedia('(display-mode: standalone)');
    patch({ installed: standalone() });
    window.addEventListener('beforeinstallprompt', prompt);
    window.addEventListener('appinstalled', installed);
    media.addEventListener('change', installed);
    if (import.meta.env.PROD && window.isSecureContext && 'serviceWorker' in navigator) {
      void navigator.serviceWorker.register('/sw.js', { scope: '/', updateViaCache: 'none' }).then(registration => {
        if (!live) return;
        patch({ registration });
        const check = () => {
          const waiting = registration.waiting;
          patch({ waiting: waiting?.state === 'installed' && registration.active ? waiting : null });
        };
        const watch = (worker: ServiceWorker | null) => {
          if (!worker) return;
          worker.addEventListener('statechange', check);
          cleanups.push(() => worker.removeEventListener('statechange', check));
        };
        const found = () => watch(registration.installing);
        registration.addEventListener('updatefound', found);
        navigator.serviceWorker.addEventListener('controllerchange', check);
        cleanups.push(() => registration.removeEventListener('updatefound', found), () => navigator.serviceWorker.removeEventListener('controllerchange', check));
        watch(registration.installing); watch(registration.waiting); check();
        // Check on foreground return without polling, and never refresh a form automatically.
        const foreground = () => { if (document.visibilityState === 'visible') void registration.update().catch(() => {}); };
        document.addEventListener('visibilitychange', foreground);
        cleanups.push(() => document.removeEventListener('visibilitychange', foreground));
      }).catch(() => patch({ error: true }));
    }
    return () => {
      live = false;
      window.removeEventListener('beforeinstallprompt', prompt);
      window.removeEventListener('appinstalled', installed);
      media.removeEventListener('change', installed);
      for (const cleanup of cleanups) cleanup();
    };
  }, []);
  return <PwaContext.Provider value={state}>{children}</PwaContext.Provider>;
}

export function PwaSettings({ language }: { language: string }) {
  const state = useContext(PwaContext);
  const ja = language === 'ja';
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function install() {
    setError('');
    if (!state.install) {
      await Dialog.alert({ title: ja ? 'ホーム画面に追加' : '添加到桌面', content: iosDevice()
        ? (ja ? 'Safariでこのページを開き、メニューの「ホーム画面に追加」を選択してください。追加後、ログインが必要な場合があります。' : '请在 Safari 打开本页，从浏览器菜单选择“添加到主屏幕”。添加后可能需要重新登录。')
        : (ja ? 'ブラウザーのメニューから「アプリをインストール」または「ホーム画面に追加」を選択してください。項目がない場合はシステムブラウザーでお試しください。' : '请从浏览器菜单选择“安装应用”或“添加到主屏幕”。没有该选项时，请使用系统浏览器打开；仍可继续使用网页版。'), confirmText: ja ? '閉じる' : '知道了' });
      return;
    }
    setBusy(true);
    try {
      await state.install.prompt();
      const choice = await state.install.userChoice;
      if (choice.outcome === 'accepted') Toast.show({ content: ja ? 'ブラウザーの案内に従ってください' : '请按浏览器提示完成安装' });
    } catch { setError(ja ? 'インストールできません。ブラウザーのメニューからお試しください。' : '暂时无法安装，请通过浏览器菜单添加到桌面。'); }
    finally { setBusy(false); }
  }
  async function update() {
    if (!state.waiting || busy) return;
    const confirmed = await Dialog.confirm({ title: ja ? '新しいバージョンに更新' : '更新到新版本', content: ja ? 'この画面を再読み込みします。入力や送信を完了し、保存されたことを確認してください。他の画面は自動更新しません。' : '更新将刷新当前页面。请先完成填报、上传或提交，并确认内容已保存；其他窗口不会自动刷新。', confirmText: ja ? '保存済み・更新する' : '已保存，更新', cancelText: ja ? '後で' : '稍后' });
    if (!confirmed) return;
    setBusy(true); setError('');
    try { await activateUpdate(state.waiting); location.reload(); }
    catch { setError(ja ? '更新できません。接続を確認して後でお試しください。' : '暂时无法更新，请确认网络后稍后重试。'); setBusy(false); }
  }
  return <section aria-label={ja ? 'アプリ' : '应用'}>
    <List>
      {!state.installed && <List.Item arrow={false} disabled={busy} onClick={() => void install()}>{ja ? 'ホーム画面に追加' : '添加到桌面'}</List.Item>}
      {state.waiting && <List.Item arrow={false} disabled={busy} onClick={() => void update()} extra={ja ? '更新あり' : '有新版本'}>{ja ? 'アプリを更新' : '更新应用'}</List.Item>}
    </List>
    {state.error && <p className="error" role="alert">{ja ? 'アプリ機能を準備できませんでした。通常のWeb版は利用できます。再接続後にページを開き直してください。' : '桌面应用功能暂未就绪，不影响正常使用网页版。联网后可重新打开页面。'}</p>}
    {error && <p className="error" role="alert">{error}</p>}
  </section>;
}
