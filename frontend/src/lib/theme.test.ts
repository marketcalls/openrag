import { applyTheme, resolveInitialTheme, storeTheme } from './theme';

afterEach(() => {
  localStorage.clear();
  document.documentElement.classList.remove('dark');
  vi.unstubAllGlobals();
});

test('the stored theme wins over the media preference', () => {
  storeTheme('dark');
  expect(resolveInitialTheme()).toBe('dark');
});

test('falls back to the operating-system color preference', () => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('dark'),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  expect(resolveInitialTheme()).toBe('dark');
});

test('applyTheme toggles dark mode on the document root', () => {
  applyTheme('dark');
  expect(document.documentElement).toHaveClass('dark');
  applyTheme('light');
  expect(document.documentElement).not.toHaveClass('dark');
});
