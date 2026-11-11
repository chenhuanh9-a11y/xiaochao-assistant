using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.Windows.Threading;

namespace StarMap.App;

/// <summary>Reusable non-activating two-stage manual map picker view.</summary>
internal sealed class ManualMapPickerWindow : Window, IDisposable
{
    const int OptionLimit = 4;
    const int ThumbnailWidth = 200;
    const int ThumbnailHeight = 116;
    const int CacheLimit = 4;
    const int GwlExStyle = -20;
    const long WsExToolWindow = 0x00000080L;
    const long WsExNoActivate = 0x08000000L;
    const int WmMouseActivate = 0x0021;
    static readonly nint MaNoActivate = (nint)3;

    sealed class OptionCard
    {
        internal readonly Button Button = new()
        {
            Margin = new Thickness(4),
            Padding = new Thickness(8),
            HorizontalContentAlignment = HorizontalAlignment.Stretch,
            VerticalContentAlignment = VerticalAlignment.Center,
            Background = Brush("#E9EFFD"),
            BorderBrush = Brush("#CAD8EE"),
            BorderThickness = new Thickness(1),
            Focusable = false
        };
        internal readonly Grid ThumbnailFrame = new()
        {
            Width = ThumbnailWidth,
            Height = ThumbnailHeight,
            HorizontalAlignment = HorizontalAlignment.Center,
            Margin = new Thickness(0, 0, 0, 4),
            Background = Brush("#F3F5FA")
        };
        internal readonly Image Thumbnail = new() { Stretch = Stretch.Uniform, Visibility = Visibility.Collapsed };
        internal readonly TextBlock Placeholder = new()
        {
            Text = "预览不可用",
            Foreground = Brush("#8491A7"),
            FontSize = 12,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
            TextAlignment = TextAlignment.Center
        };
        internal readonly TextBlock Label = new()
        {
            Foreground = Brush("#1F50CC"),
            FontSize = 13,
            TextAlignment = TextAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis,
            VerticalAlignment = VerticalAlignment.Center
        };
        internal readonly StackPanel ContentPanel = new() { VerticalAlignment = VerticalAlignment.Center };
        internal ManualMapPickerOption? Option;
        internal ThumbnailKey? ThumbnailIdentity;

        static Brush Brush(string color) => new BrushConverter().ConvertFromString(color) is Brush brush
            ? brush
            : Brushes.Transparent;

        internal OptionCard(int index)
        {
            ThumbnailFrame.Children.Add(Thumbnail);
            ThumbnailFrame.Children.Add(Placeholder);
            ContentPanel.Children.Add(ThumbnailFrame);
            ContentPanel.Children.Add(Label);
            Button.Content = ContentPanel;
            Button.Name = $"ManualPickerOption{index}";
            AutomationProperties.SetAutomationId(Button, Button.Name);
            AutomationProperties.SetName(Button, $"F{index + 1} 地图选项");
        }

        internal void Configure(ManualMapPickerOption? option, bool busy)
        {
            Option = option;
            Button.Visibility = option is null ? Visibility.Collapsed : Visibility.Visible;
            if (option is null) return;

            bool isMap = option.MapId is not null;
            string count = isMap ? string.Empty : $"（{option.Count} 张）";
            string text = $"{option.Key} · {option.Label}{count}";
            Label.Text = text;
            Label.Foreground = busy ? Brush("#8491A7") : Brush("#1F50CC");
            Button.Height = isMap ? 174 : 58;
            Button.IsEnabled = !busy;
            ThumbnailFrame.Visibility = isMap ? Visibility.Visible : Visibility.Collapsed;
            AutomationProperties.SetName(Button, text);

            ThumbnailKey? nextIdentity = null;
            if (isMap && TryGetKey(option, out var key)) nextIdentity = key;
            if (nextIdentity != ThumbnailIdentity)
            {
                ThumbnailIdentity = nextIdentity;
                ClearThumbnail();
            }
        }

        internal void ClearThumbnail()
        {
            Thumbnail.Source = null;
            Thumbnail.Visibility = Visibility.Collapsed;
            Placeholder.Visibility = Visibility.Visible;
        }

        internal void SetThumbnail(BitmapSource? bitmap)
        {
            Thumbnail.Source = bitmap;
            Thumbnail.Visibility = bitmap is null ? Visibility.Collapsed : Visibility.Visible;
            Placeholder.Visibility = bitmap is null ? Visibility.Visible : Visibility.Collapsed;
        }
    }

    internal readonly record struct ThumbnailKey(string Path, string Stamp);
    sealed record ThumbnailRequest(int CardIndex, ThumbnailKey Key, string Path);
    sealed record ThumbnailResult(ThumbnailRequest Request, BitmapSource? Bitmap);
    sealed record ThumbnailBatch(int Generation, ThumbnailRequest[] Requests);
    sealed record ThumbnailCacheItem(ThumbnailKey Key, BitmapSource Bitmap);

    readonly OptionCard[] cards = Enumerable.Range(0, OptionLimit).Select(index => new OptionCard(index)).ToArray();
    readonly LinkedList<ThumbnailCacheItem> thumbnailCache = new();
    readonly Button pageUpButton = NavButton("上一页");
    readonly Button backButton = NavButton("返回分组");
    readonly Button pageDownButton = NavButton("下一页");
    readonly Button closeButton = NavButton("关闭");
    readonly TextBlock titleText = new() { FontSize = 19, FontWeight = FontWeights.SemiBold, Foreground = Brush("#142B4B"), VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock descriptionText = new() { FontSize = 12, Foreground = Brush("#526785"), TextWrapping = TextWrapping.Wrap };
    readonly TextBlock pageText = new() { FontSize = 11, Foreground = Brush("#526785"), TextAlignment = TextAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
    readonly TextBlock helpTextBlock = new() { FontSize = 11, Foreground = Brush("#526785"), TextWrapping = TextWrapping.Wrap };
    readonly TextBlock busyText = new() { Text = "正在载入地图选项…", FontSize = 11, Foreground = Brush("#B06D00"), TextWrapping = TextWrapping.Wrap, Visibility = Visibility.Collapsed };
    readonly Grid optionsGrid = new() { HorizontalAlignment = HorizontalAlignment.Stretch };
    readonly Func<string, CancellationToken, BitmapSource> loadImage;
    IReadOnlyList<ManualMapPickerOption> currentOptions = Array.Empty<ManualMapPickerOption>();
    CancellationTokenSource? thumbnailCancellation;
    Task? thumbnailWorker;
    // Keep one active decode batch and replace this single slot as pages change.
    ThumbnailBatch? pendingThumbnailBatch;
    HwndSource? hwndSource;
    HwndSourceHook? hwndHook;
    int thumbnailGeneration;
    int thumbnailWorkGeneration = -1;
    int disposeRequested;
    bool thumbnailRequestPending;

    internal event Action<int>? OptionRequested;
    internal event Action<string>? NavigationRequested;
    internal event Action? CloseRequested;

    internal ManualMapPickerWindow(Func<string, CancellationToken, BitmapSource>? loadImage = null)
    {
        this.loadImage = loadImage ?? ResourcePreview.LoadImage;
        Width = 620;
        MinWidth = 620;
        MaxWidth = 620;
        MinHeight = 250;
        MaxHeight = 680;
        SizeToContent = System.Windows.SizeToContent.Height;
        WindowStyle = System.Windows.WindowStyle.None;
        ResizeMode = System.Windows.ResizeMode.NoResize;
        ShowInTaskbar = false;
        ShowActivated = false;
        Topmost = true;
        AllowsTransparency = true;
        Background = Brushes.Transparent;
        WindowStartupLocation = WindowStartupLocation.Manual;
        Title = "手动选图";
        FontFamily = new FontFamily("Microsoft YaHei UI");
        NameScope.SetNameScope(this, new NameScope());
        if (TryFindResource("MaterialDesignWindow") is Style materialWindowStyle)
            Style = materialWindowStyle;

        Content = BuildContent();
        RegisterName(closeButton.Name, closeButton);
        RegisterName(backButton.Name, backButton);
        RegisterName(pageUpButton.Name, pageUpButton);
        RegisterName(pageDownButton.Name, pageDownButton);
        for (int i = 0; i < cards.Length; i++)
        {
            int index = i;
            RegisterName(cards[i].Button.Name, cards[i].Button);
            cards[i].Button.Click += (_, _) =>
            {
                if (Volatile.Read(ref disposeRequested) == 0 && cards[index].Option is not null)
                    OptionRequested?.Invoke(index);
            };
            Grid.SetColumn(cards[i].Button, i % 2);
            Grid.SetRow(cards[i].Button, i / 2);
            optionsGrid.Children.Add(cards[i].Button);
        }

        pageUpButton.Click += (_, _) => NavigationRequested?.Invoke("PageUp");
        pageDownButton.Click += (_, _) => NavigationRequested?.Invoke("PageDown");
        backButton.Click += (_, _) => NavigationRequested?.Invoke("Backspace");
        closeButton.Click += (_, _) => CloseRequested?.Invoke();
        Closing += OnClosing;
        SourceInitialized += OnSourceInitialized;
        Closed += OnClosed;
        IsVisibleChanged += OnVisibilityChanged;
    }

    internal void Render(string title, string description, string helpText, string pageLabel,
        IReadOnlyList<ManualMapPickerOption> options, bool busy,
        (bool Back, bool Previous, bool Next) navigation = default)
    {
        Dispatcher.VerifyAccess();
        if (Volatile.Read(ref disposeRequested) != 0) return;
        ArgumentNullException.ThrowIfNull(options);
        if (options.Count > OptionLimit) throw new ArgumentOutOfRangeException(nameof(options), "手动选图每页最多显示四项。");

        var snapshot = options.ToArray();
        bool contextChanged = !currentOptions.SequenceEqual(snapshot);
        if (contextChanged)
        {
            currentOptions = Array.AsReadOnly(snapshot);
            ResetThumbnailContext();
        }

        Title = title ?? string.Empty;
        titleText.Text = Title;
        descriptionText.Text = description ?? string.Empty;
        helpTextBlock.Text = helpText ?? string.Empty;
        pageText.Text = pageLabel ?? string.Empty;
        busyText.Visibility = busy ? Visibility.Visible : Visibility.Collapsed;
        ConfigurePaging(navigation, busy);

        for (int i = 0; i < cards.Length; i++)
        {
            var option = i < snapshot.Length ? snapshot[i] : null;
            cards[i].Configure(option, busy);
            if (contextChanged && option?.MapId is not null && TryGetKey(option, out var key)
                && TryGetThumbnail(key, out var cached))
                cards[i].SetThumbnail(cached);
        }

        if (IsVisible)
        {
            thumbnailRequestPending = false;
            StartThumbnailLoads(snapshot);
        }
        else thumbnailRequestPending = true;
    }

    internal async Task DrainImagesAsync()
    {
        while (!Dispatcher.HasShutdownStarted && !Dispatcher.HasShutdownFinished)
        {
            Task? worker;
            try
            {
                worker = await Dispatcher.InvokeAsync(() => thumbnailWorker,
                    DispatcherPriority.Background).Task.ConfigureAwait(false);
            }
            catch (InvalidOperationException) { return; }
            catch (TaskCanceledException) { return; }

            if (worker is null) return;
            await worker.ConfigureAwait(false);
        }
    }

    FrameworkElement BuildContent()
    {
        var content = new StackPanel { Margin = new Thickness(18) };
        var header = new Grid { Margin = new Thickness(0, 0, 0, 4) };
        header.ColumnDefinitions.Add(new ColumnDefinition());
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.Children.Add(titleText);
        closeButton.Content = "关闭  Esc";
        closeButton.Name = "ManualPickerClose";
        AutomationProperties.SetAutomationId(closeButton, closeButton.Name);
        closeButton.MinWidth = 92;
        closeButton.Margin = new Thickness(8, 0, 0, 0);
        Grid.SetColumn(closeButton, 1);
        header.Children.Add(closeButton);
        content.Children.Add(header);

        descriptionText.Margin = new Thickness(0, 0, 0, 8);
        content.Children.Add(descriptionText);

        optionsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        optionsGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        optionsGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        optionsGrid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        optionsGrid.Margin = new Thickness(-4, 0, -4, 2);
        content.Children.Add(optionsGrid);

        pageText.Margin = new Thickness(0, 2, 0, 5);
        content.Children.Add(pageText);

        var navigation = new Grid { Margin = new Thickness(0, 0, 0, 4) };
        navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        navigation.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        backButton.Name = "ManualPickerBack";
        pageUpButton.Name = "ManualPickerPrevious";
        pageDownButton.Name = "ManualPickerNext";
        AutomationProperties.SetAutomationId(backButton, backButton.Name);
        AutomationProperties.SetAutomationId(pageUpButton, pageUpButton.Name);
        AutomationProperties.SetAutomationId(pageDownButton, pageDownButton.Name);
        backButton.Margin = new Thickness(0, 0, 6, 0);
        pageUpButton.Margin = new Thickness(0, 0, 6, 0);
        pageDownButton.Margin = new Thickness(6, 0, 0, 0);
        Grid.SetColumn(backButton, 0);
        Grid.SetColumn(pageUpButton, 1);
        Grid.SetColumn(pageDownButton, 2);
        navigation.Children.Add(backButton);
        navigation.Children.Add(pageUpButton);
        navigation.Children.Add(pageDownButton);
        content.Children.Add(navigation);

        helpTextBlock.Margin = new Thickness(0, 2, 0, 0);
        content.Children.Add(helpTextBlock);
        busyText.Margin = new Thickness(0, 5, 0, 0);
        content.Children.Add(busyText);

        return new Border
        {
            Margin = new Thickness(5),
            Padding = new Thickness(0),
            CornerRadius = new CornerRadius(14),
            BorderThickness = new Thickness(1),
            BorderBrush = Brush("#D6E0EF"),
            Background = Brush("#F7FAFF"),
            Child = content
        };
    }

    void ConfigurePaging((bool Back, bool Previous, bool Next) navigation, bool busy)
    {
        backButton.IsEnabled = !busy && navigation.Back;
        pageUpButton.IsEnabled = !busy && navigation.Previous;
        pageDownButton.IsEnabled = !busy && navigation.Next;
    }

    void ResetThumbnailContext()
    {
        thumbnailGeneration++;
        thumbnailWorkGeneration = -1;
        pendingThumbnailBatch = null;
        thumbnailRequestPending = true;
        Cancel(thumbnailCancellation);
        foreach (var card in cards)
        {
            card.ThumbnailIdentity = null;
            card.ClearThumbnail();
        }
    }

    void StartThumbnailLoads(IReadOnlyList<ManualMapPickerOption> options)
    {
        if (thumbnailWorkGeneration == thumbnailGeneration) return;
        thumbnailWorkGeneration = thumbnailGeneration;
        var pending = new List<ThumbnailRequest>(OptionLimit);
        for (int i = 0; i < options.Count; i++)
        {
            var option = options[i];
            if (option.MapId is null || !TryGetKey(option, out var key)) continue;
            if (TryGetThumbnail(key, out var cached))
            {
                cards[i].SetThumbnail(cached);
                continue;
            }
            pending.Add(new(i, key, option.ThumbnailPath!));
        }
        if (pending.Count == 0)
        {
            pendingThumbnailBatch = null;
            return;
        }

        var batch = new ThumbnailBatch(thumbnailGeneration, pending.ToArray());
        if (thumbnailWorker is not null) pendingThumbnailBatch = batch;
        else StartThumbnailBatch(batch);
    }

    void StartThumbnailBatch(ThumbnailBatch batch)
    {
        if (Volatile.Read(ref disposeRequested) != 0 || !IsVisible || batch.Generation != thumbnailGeneration) return;
        var cancellation = new CancellationTokenSource();
        thumbnailCancellation = cancellation;
        thumbnailWorker = LoadThumbnailBatchAsync(batch, cancellation);
    }

    async Task LoadThumbnailBatchAsync(ThumbnailBatch batch, CancellationTokenSource cancellation)
    {
        var token = cancellation.Token;
        ThumbnailResult[]? results = null;
        try
        {
            var jobs = batch.Requests.Select(request => Task.Run(() => LoadThumbnail(request.Path, token), token)).ToArray();
            BitmapSource?[] images = await Task.WhenAll(jobs).ConfigureAwait(false);
            token.ThrowIfCancellationRequested();
            results = batch.Requests.Select((request, index) => new ThumbnailResult(request, images[index])).ToArray();
        }
        catch (OperationCanceledException) { }
        catch (Exception) { }
        finally
        {
            if (!Dispatcher.HasShutdownStarted && !Dispatcher.HasShutdownFinished)
            {
                try
                {
                    await Dispatcher.InvokeAsync(
                        () => FinishThumbnailBatch(batch.Generation, cancellation, results), DispatcherPriority.Background)
                        .Task.ConfigureAwait(false);
                }
                catch (InvalidOperationException) { }
                catch (TaskCanceledException) { }
            }
            cancellation.Dispose();
        }
    }

    void FinishThumbnailBatch(int generation, CancellationTokenSource cancellation, IReadOnlyList<ThumbnailResult>? results)
    {
        if (!ReferenceEquals(thumbnailCancellation, cancellation)) return;

        if (results is not null && Volatile.Read(ref disposeRequested) == 0
            && IsVisible && generation == thumbnailGeneration)
        {
            foreach (var result in results)
            {
                if (result.Bitmap is not null) RememberThumbnail(result.Request.Key, result.Bitmap);
                int index = result.Request.CardIndex;
                if ((uint)index >= (uint)cards.Length || cards[index].Option?.MapId is null
                    || cards[index].ThumbnailIdentity != result.Request.Key)
                    continue;
                cards[index].SetThumbnail(result.Bitmap);
            }
        }

        thumbnailCancellation = null;
        thumbnailWorker = null;
        if (Volatile.Read(ref disposeRequested) != 0 || !IsVisible)
        {
            pendingThumbnailBatch = null;
            return;
        }

        var pending = pendingThumbnailBatch;
        pendingThumbnailBatch = null;
        if (pending is not null && pending.Generation == thumbnailGeneration)
            StartThumbnailBatch(pending);
    }

    BitmapSource? LoadThumbnail(string path, CancellationToken cancellation)
    {
        try
        {
            var source = loadImage(path, cancellation);
            if (!source.IsFrozen)
            {
                if (!source.CanFreeze) return null;
                source.Freeze();
            }
            cancellation.ThrowIfCancellationRequested();
            if (source.PixelWidth <= 0 || source.PixelHeight <= 0) return null;
            double scale = Math.Min(1d, Math.Min((double)ThumbnailWidth / source.PixelWidth,
                (double)ThumbnailHeight / source.PixelHeight));
            if (scale >= 1d) return source;
            var resized = new TransformedBitmap(source, new ScaleTransform(scale, scale));
            cancellation.ThrowIfCancellationRequested();
            if (!resized.CanFreeze) return source;
            resized.Freeze();
            return resized;
        }
        catch (OperationCanceledException) { throw; }
        catch (Exception) { return null; }
    }

    bool TryGetThumbnail(ThumbnailKey key, out BitmapSource? bitmap)
    {
        var node = thumbnailCache.First;
        while (node is not null)
        {
            var next = node.Next;
            if (node.Value.Key == key)
            {
                thumbnailCache.Remove(node);
                thumbnailCache.AddFirst(node);
                bitmap = node.Value.Bitmap;
                return true;
            }
            node = next;
        }
        bitmap = null;
        return false;
    }

    void RememberThumbnail(ThumbnailKey key, BitmapSource bitmap)
    {
        var node = thumbnailCache.First;
        while (node is not null)
        {
            var next = node.Next;
            if (node.Value.Key == key) thumbnailCache.Remove(node);
            node = next;
        }
        thumbnailCache.AddFirst(new ThumbnailCacheItem(key, bitmap));
        while (thumbnailCache.Count > CacheLimit) thumbnailCache.RemoveLast();
    }

    static bool TryGetKey(ManualMapPickerOption option, out ThumbnailKey key)
        => TryGetKey(option.ThumbnailPath, option.ThumbnailStamp, out key);

    static bool TryGetKey(string? path, string? stamp, out ThumbnailKey key)
    {
        key = default;
        if (string.IsNullOrWhiteSpace(path)) return false;
        try { key = new(Path.GetFullPath(path), stamp ?? string.Empty); return true; }
        catch (Exception e) when (e is ArgumentException or IOException or NotSupportedException or UnauthorizedAccessException)
        { return false; }
    }

    static void Cancel(CancellationTokenSource? source)
    {
        if (source is null) return;
        try { source.Cancel(); }
        catch (ObjectDisposedException) { }
    }

    void OnClosing(object? sender, CancelEventArgs e)
    {
        if (Volatile.Read(ref disposeRequested) != 0) return;
        e.Cancel = true;
        CloseRequested?.Invoke();
    }

    void OnVisibilityChanged(object sender, DependencyPropertyChangedEventArgs e)
    {
        if (Volatile.Read(ref disposeRequested) != 0) return;
        if (IsVisible)
        {
            if (thumbnailRequestPending || thumbnailWorkGeneration != thumbnailGeneration)
            {
                thumbnailRequestPending = false;
                StartThumbnailLoads(currentOptions);
            }
            return;
        }

        thumbnailGeneration++;
        thumbnailWorkGeneration = -1;
        pendingThumbnailBatch = null;
        thumbnailRequestPending = true;
        Cancel(thumbnailCancellation);
    }

    void OnSourceInitialized(object? sender, EventArgs e)
    {
        if (Volatile.Read(ref disposeRequested) != 0) return;
        nint handle = new WindowInteropHelper(this).Handle;
        hwndSource = HwndSource.FromHwnd(handle);
        if (hwndSource is null) return;
        hwndHook = WindowProcedure;
        hwndSource.AddHook(hwndHook);

        nint existing = GetWindowLongPointer(handle, GwlExStyle);
        const long WsExAppWindow = 0x00040000L;
        nint required = (nint)((existing.ToInt64() | WsExNoActivate | WsExToolWindow) & ~WsExAppWindow);
        Marshal.SetLastPInvokeError(0);
        nint previous = SetWindowLongPointer(handle, GwlExStyle, required);
        if (previous == 0 && Marshal.GetLastWin32Error() != 0)
            throw new Win32Exception(Marshal.GetLastWin32Error(), "设置选图窗口的非激活/工具窗口样式失败。");
    }

    nint WindowProcedure(nint hwnd, int message, nint wParam, nint lParam, ref bool handled)
    {
        if (message == WmMouseActivate)
        {
            handled = true;
            return MaNoActivate;
        }
        return 0;
    }

    void OnClosed(object? sender, EventArgs e)
    {
        if (hwndSource is not null && hwndHook is not null) hwndSource.RemoveHook(hwndHook);
        hwndSource = null;
        hwndHook = null;
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposeRequested, 1) != 0) return;
        Cancel(thumbnailCancellation);
        if (Dispatcher.HasShutdownStarted || Dispatcher.HasShutdownFinished) return;
        if (Dispatcher.CheckAccess()) CompleteDispose();
        else _ = Dispatcher.BeginInvoke(DispatcherPriority.Send, new Action(CompleteDispose));
    }

    void CompleteDispose()
    {
        thumbnailGeneration++;
        thumbnailWorkGeneration = -1;
        pendingThumbnailBatch = null;
        thumbnailRequestPending = false;
        foreach (var card in cards)
        {
            card.Thumbnail.Source = null;
            card.Option = null;
        }
        thumbnailCache.Clear();
        if (!Dispatcher.HasShutdownStarted && !Dispatcher.HasShutdownFinished) Close();
    }

    static Button NavButton(string text) => new()
    {
        Content = text,
        MinHeight = 32,
        MinWidth = 72,
        Padding = new Thickness(9, 4, 9, 4),
        FontSize = 12,
        Focusable = false
    };

    static Brush Brush(string color) => new BrushConverter().ConvertFromString(color) is Brush brush
        ? brush
        : Brushes.Transparent;

    static nint GetWindowLongPointer(nint handle, int index)
        => IntPtr.Size == 8 ? GetWindowLongPtr64(handle, index) : (nint)GetWindowLong32(handle, index);

    static nint SetWindowLongPointer(nint handle, int index, nint value)
        => IntPtr.Size == 8 ? SetWindowLongPtr64(handle, index, value) : (nint)SetWindowLong32(handle, index, (int)value);

    [DllImport("user32.dll", EntryPoint = "GetWindowLongW", SetLastError = true)]
    static extern int GetWindowLong32(nint handle, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    static extern int SetWindowLong32(nint handle, int index, int value);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW", SetLastError = true)]
    static extern nint GetWindowLongPtr64(nint handle, int index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    static extern nint SetWindowLongPtr64(nint handle, int index, nint value);
}
