# This STA helper exchanges one JSON snapshot or action per line with main_bg.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Add-Type -AssemblyName PresentationFramework
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$inputStream = [IO.StreamReader]::new([Console]::OpenStandardInput(), [Text.Encoding]::UTF8)
$window = [Windows.Window]::new()
$window.Title = 'AstrBot'
$window.WindowStyle = 'None'
$window.AllowsTransparency = $true
$window.Background = [Windows.Media.Brushes]::Transparent
$window.ShowActivated = $false
$window.ShowInTaskbar = $false
$window.Topmost = $true
$window.ResizeMode = 'NoResize'
$window.SizeToContent = 'Height'
$window.Width = [Math]::Min(400, [Windows.SystemParameters]::WorkArea.Width - 24)
$window.FontFamily = [Windows.Media.FontFamily]::new('Segoe UI, Microsoft YaHei UI')
$scroll = [Windows.Controls.ScrollViewer]::new()
$scroll.MaxHeight = [Windows.SystemParameters]::WorkArea.Height * 0.6
$scroll.VerticalScrollBarVisibility = 'Auto'
$scroll.HorizontalScrollBarVisibility = 'Disabled'
$stack = [Windows.Controls.StackPanel]::new()
$scroll.Content = $stack
$window.Content = $scroll
$window.add_SizeChanged({
    $area = [Windows.SystemParameters]::WorkArea
    $window.Left = $area.Right - $window.ActualWidth - 12
    $window.Top = $area.Bottom - $window.ActualHeight - 12
})
$dark = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' -Name AppsUseLightTheme -ErrorAction SilentlyContinue).AppsUseLightTheme -eq 0
$theme = if ($dark) {
    @{Paper='#282622'; Ink='#FAF5ED'; Muted='#C7BEB0'; Line='#695435'; Tile='#4E412B'; Accent='#F2BD68'; Link='#98CFF0'}
} else {
    @{Paper='#FFFCF6'; Ink='#2D2A25'; Muted='#675F51'; Line='#E1CCAB'; Tile='#F9EACE'; Accent='#926120'; Link='#236B99'}
}
$converter = [Windows.Media.BrushConverter]::new()
foreach ($name in $theme.Keys) { $window.Resources[$name] = $converter.ConvertFromString($theme[$name]) }
$strings = switch ([Globalization.CultureInfo]::CurrentUICulture.TwoLetterISOLanguageName) {
    'zh' { @('待办提醒','查看待办','关闭提醒') }
    'ja' { @('タスクの通知','タスクを開く','通知を閉じる') }
    'ru' { @('Напоминание','Открыть задачу','Закрыть напоминание') }
    default { @('Task reminder','View task','Dismiss reminder') }
}
$template = @'
<Border xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
 xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml" Margin="4" CornerRadius="14" Background="{DynamicResource Paper}"
 BorderBrush="{DynamicResource Line}" BorderThickness="1" TextElement.Foreground="{DynamicResource Ink}">
 <Border.Resources>
   <Storyboard x:Key="Ring">
     <DoubleAnimationUsingKeyFrames Storyboard.TargetName="Tilt" Storyboard.TargetProperty="Angle" Duration="0:0:1" RepeatBehavior="10x" FillBehavior="Stop">
       <LinearDoubleKeyFrame KeyTime="0:0:0" Value="0"/><LinearDoubleKeyFrame KeyTime="0:0:0.1" Value="-19"/>
       <LinearDoubleKeyFrame KeyTime="0:0:0.23" Value="16"/><LinearDoubleKeyFrame KeyTime="0:0:0.36" Value="-11"/>
       <LinearDoubleKeyFrame KeyTime="0:0:0.48" Value="7"/><LinearDoubleKeyFrame KeyTime="0:0:0.65" Value="0"/>
     </DoubleAnimationUsingKeyFrames>
     <DoubleAnimation Storyboard.TargetName="Pulse" Storyboard.TargetProperty="Opacity" From="0" To="0.65"
       Duration="0:0:1" AutoReverse="True" RepeatBehavior="5x" FillBehavior="Stop"/>
   </Storyboard>
   <Style TargetType="Button">
     <Setter Property="Cursor" Value="Hand"/><Setter Property="Background" Value="Transparent"/>
     <Setter Property="Foreground" Value="{DynamicResource Link}"/>
     <Setter Property="Template"><Setter.Value><ControlTemplate TargetType="Button">
       <Border x:Name="ButtonFace" Padding="6,4" CornerRadius="5" Background="{TemplateBinding Background}">
         <ContentPresenter HorizontalAlignment="Center" VerticalAlignment="Center"/>
       </Border>
       <ControlTemplate.Triggers><Trigger Property="IsMouseOver" Value="True">
         <Setter TargetName="ButtonFace" Property="Background" Value="{DynamicResource Tile}"/>
       </Trigger></ControlTemplate.Triggers>
     </ControlTemplate></Setter.Value></Setter>
   </Style>
 </Border.Resources>
 <Grid>
   <Border x:Name="Pulse" IsHitTestVisible="False" BorderBrush="{DynamicResource Accent}" BorderThickness="2" CornerRadius="13" Opacity="0"/>
   <Grid Margin="14,12,9,11">
     <Grid.ColumnDefinitions><ColumnDefinition Width="42"/><ColumnDefinition Width="*"/><ColumnDefinition Width="28"/></Grid.ColumnDefinitions>
     <Border Height="40" Width="40" Background="{DynamicResource Tile}" CornerRadius="12" VerticalAlignment="Top">
       <Viewbox Width="24" Height="24" RenderTransformOrigin="0.5,0.18"><Viewbox.RenderTransform><RotateTransform x:Name="Tilt"/></Viewbox.RenderTransform>
         <Canvas Width="24" Height="24"><Path Stroke="{DynamicResource Accent}" StrokeThickness="1.8" StrokeLineJoin="Round" StrokeStartLineCap="Round" StrokeEndLineCap="Round"
           Data="M5,17 C7,14 6,12 7,8 C8,3 16,3 17,8 C18,12 17,14 19,17 Z M10,20 Q12,23 14,20 M2,9 Q2,5 5,3 M22,9 Q22,5 19,3"/></Canvas>
       </Viewbox>
     </Border>
     <StackPanel Grid.Column="1" Margin="10,0,4,0">
       <TextBlock x:Name="Caption" FontSize="12" FontWeight="SemiBold" Foreground="{DynamicResource Accent}"/>
       <TextBlock x:Name="TaskTitle" TextWrapping="Wrap" FontSize="16" FontWeight="SemiBold" Margin="0,4,0,3"/>
       <ScrollViewer MaxHeight="112" VerticalScrollBarVisibility="Auto"><TextBlock x:Name="Description" TextWrapping="Wrap" FontSize="13" Foreground="{DynamicResource Muted}"/></ScrollViewer>
       <DockPanel Margin="0,8,0,0" LastChildFill="True">
         <Button x:Name="OpenTask" DockPanel.Dock="Right"/>
         <TextBlock x:Name="Project" TextTrimming="CharacterEllipsis" VerticalAlignment="Center" Foreground="{DynamicResource Muted}" FontSize="12"/>
       </DockPanel>
     </StackPanel>
     <Button x:Name="Dismiss" Grid.Column="2" Height="28" VerticalAlignment="Top" FontSize="17" Content="×" Foreground="{DynamicResource Muted}"/>
   </Grid>
 </Grid>
</Border>
'@
$cards = @{}
$timer = [Windows.Threading.DispatcherTimer]::new()
$timer.Interval = [TimeSpan]::FromMilliseconds(100)
$script:pending = $inputStream.ReadLineAsync()
$timer.add_Tick({
    if (!$script:pending.IsCompleted) { return }
    $line = $script:pending.GetAwaiter().GetResult()
    if ($null -eq $line) { $timer.Stop(); $window.Hide(); $window.Dispatcher.InvokeShutdown(); return }
    $script:pending = $inputStream.ReadLineAsync()
    $incoming = @{}
    $index = 0
    foreach ($notice in (ConvertFrom-Json -InputObject $line)) {
        $key = "$($notice.id)/$($notice.unread_at)"
        $incoming[$key] = $true
        if (!$cards.ContainsKey($key)) {
            $card = [Windows.Markup.XamlReader]::Parse($template)
            $card.FindName('Caption').Text = "AstrBot · $($strings[0])"
            $open = $card.FindName('OpenTask')
            $open.Content = $strings[1]
            $open.Tag = $notice
            $open.add_Click({param($sender,$event)
                [Console]::WriteLine((@{action='open'; id=$sender.Tag.id; at=$sender.Tag.unread_at} | ConvertTo-Json -Compress))
            })
            $close = $card.FindName('Dismiss')
            $close.ToolTip = $strings[2]
            [Windows.Automation.AutomationProperties]::SetName($close, $strings[2])
            $close.Tag = $notice
            $close.add_Click({param($sender,$event)
                [Console]::WriteLine((@{action='close'; id=$sender.Tag.id; at=$sender.Tag.unread_at} | ConvertTo-Json -Compress))
                $key = "$($sender.Tag.id)/$($sender.Tag.unread_at)"
                $stack.Children.Remove($cards[$key]); $cards.Remove($key)
                if (!$cards.Count) { $window.Hide() }
            })
            $cards[$key] = $card
            $stack.Children.Insert($index, $card)
            if ([Windows.SystemParameters]::ClientAreaAnimation) { $card.Resources['Ring'].Begin($card) }
        }
        $card = $cards[$key]
        $card.FindName('TaskTitle').Text = $notice.title
        $card.FindName('Description').Text = $notice.description
        $card.FindName('Project').Text = $notice.project
        $index++
    }
    foreach ($key in @($cards.Keys)) {
        if (!$incoming.ContainsKey($key)) { $stack.Children.Remove($cards[$key]); $cards.Remove($key) }
    }
    if ($cards.Count -and !$window.IsVisible) { $window.Show() }
    elseif (!$cards.Count -and $window.IsVisible) { $window.Hide() }
})
$window.add_Closing({param($sender,$event) $event.Cancel = $true})
$timer.Start()
[Windows.Threading.Dispatcher]::Run()
