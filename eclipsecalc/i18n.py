"""Messages of the web API in the language of the page.

The Japanese text is the message itself (and the key of its translations).  The page sends its
language in the ``X-Lang`` header; the server keeps it for the request in a context variable,
so that errors and warnings made anywhere during the computation follow it.  Without a language
(cli.py, tests) the messages stay Japanese.
"""
import contextvars

LANGUAGES = ('ja', 'en', 'fr', 'ru', 'es', 'zh', 'hi')
_lang = contextvars.ContextVar('lang', default='ja')


def normalize(lang):
    lang = (lang or '').strip().lower().replace('_', '-').split('-')[0]
    return lang if lang in LANGUAGES else 'ja'


def set_lang(lang):
    return _lang.set(normalize(lang))


def reset_lang(token):
    _lang.reset(token)


def current():
    return _lang.get()


def tr(text, **kw):
    """``text`` (Japanese, with ``str.format`` fields) in the language of the request."""
    lang = _lang.get()
    if lang != 'ja':
        text = MESSAGES.get(text, {}).get(lang) or text
    return text.format(**kw) if kw else text


MESSAGES = {
    # ---- server.py
    '指定された現象が見つかりません（サーバー再起動後は再計算してください）': {
        'en': 'The requested event was not found (compute again after the server was restarted)',
        'fr': 'Événement introuvable (relancez le calcul après un redémarrage du serveur)',
        'ru': 'Событие не найдено (после перезапуска сервера выполните расчёт заново)',
        'es': 'No se encontró el evento (vuelva a calcular si el servidor se reinició)',
        'zh': '找不到指定的天象（服务器重启后请重新计算）',
        'hi': 'माँगी गई घटना नहीं मिली (सर्वर फिर से शुरू होने के बाद दोबारा गणना करें)',
    },
    '{name} は自動ダウンロードに対応していません': {
        'en': '{name} cannot be downloaded automatically',
        'fr': '{name} ne peut pas être téléchargé automatiquement',
        'ru': '{name} нельзя загрузить автоматически',
        'es': '{name} no se puede descargar automáticamente',
        'zh': '{name} 不支持自动下载',
        'hi': '{name} अपने-आप डाउनलोड नहीं किया जा सकता',
    },
    'JPL 暦をダウンロードできませんでした: {exc}': {
        'en': 'Could not download the JPL ephemeris: {exc}',
        'fr': 'Impossible de télécharger l’éphéméride JPL : {exc}',
        'ru': 'Не удалось загрузить эфемериды JPL: {exc}',
        'es': 'No se pudo descargar la efeméride JPL: {exc}',
        'zh': '无法下载 JPL 历表：{exc}',
        'hi': 'JPL एफ़ेमेरिस डाउनलोड नहीं हो सका: {exc}',
    },
    'CelesTrak に接続できませんでした: {exc}': {
        'en': 'Could not connect to CelesTrak: {exc}',
        'fr': 'Connexion à CelesTrak impossible : {exc}',
        'ru': 'Не удалось подключиться к CelesTrak: {exc}',
        'es': 'No se pudo conectar con CelesTrak: {exc}',
        'zh': '无法连接 CelesTrak：{exc}',
        'hi': 'CelesTrak से कनेक्ट नहीं हो सका: {exc}',
    },
    '入力値を解釈できません: {exc}': {
        'en': 'Cannot interpret the input: {exc}',
        'fr': 'Saisie incompréhensible : {exc}',
        'ru': 'Не удаётся разобрать введённые данные: {exc}',
        'es': 'No se puede interpretar la entrada: {exc}',
        'zh': '无法解析输入值：{exc}',
        'hi': 'इनपुट समझ में नहीं आया: {exc}',
    },
    '終了日は開始日より後にしてください': {
        'en': 'The end date must be after the start date',
        'fr': 'La date de fin doit être postérieure à la date de début',
        'ru': 'Дата окончания должна быть позже даты начала',
        'es': 'La fecha final debe ser posterior a la inicial',
        'zh': '结束日期必须晚于开始日期',
        'hi': 'अंतिम तिथि आरंभ तिथि के बाद होनी चाहिए',
    },
    '期間が暦 {name} の範囲（{start}〜{end}）を超えています': {
        'en': 'The period is outside the range of the ephemeris {name} ({start} – {end})',
        'fr': 'La période dépasse l’étendue de l’éphéméride {name} ({start} – {end})',
        'ru': 'Период выходит за пределы эфемерид {name} ({start} – {end})',
        'es': 'El periodo excede el rango de la efeméride {name} ({start} – {end})',
        'zh': '时间范围超出了历表 {name} 的范围（{start}～{end}）',
        'hi': 'अवधि एफ़ेमेरिस {name} की सीमा ({start} – {end}) से बाहर है',
    },
    '計算する現象を 1 つ以上選んでください': {
        'en': 'Select at least one phenomenon to compute',
        'fr': 'Choisissez au moins un phénomène à calculer',
        'ru': 'Выберите хотя бы одно явление для расчёта',
        'es': 'Seleccione al menos un fenómeno para calcular',
        'zh': '请至少选择一种要计算的天象',
        'hi': 'गणना के लिए कम से कम एक घटना चुनें',
    },
    '地球全体': {
        'en': 'Whole Earth', 'fr': 'Terre entière', 'ru': 'Вся Земля', 'es': 'Toda la Tierra',
        'zh': '全球', 'hi': 'पूरी पृथ्वी',
    },
    '人工衛星の観測者では期間を 20 年以内にしてください': {
        'en': 'For a satellite observer the period must be 20 years or less',
        'fr': 'Pour un satellite, la période doit être de 20 ans au plus',
        'ru': 'Для спутника период не должен превышать 20 лет',
        'es': 'Para un satélite el periodo debe ser de 20 años como máximo',
        'zh': '以人造卫星为观测者时，时间范围须在 20 年以内',
        'hi': 'उपग्रह प्रेक्षक के लिए अवधि 20 वर्ष या उससे कम होनी चाहिए',
    },
    '計算中にエラーが発生しました: {exc}': {
        'en': 'An error occurred during the computation: {exc}',
        'fr': 'Une erreur s’est produite pendant le calcul : {exc}',
        'ru': 'Во время расчёта произошла ошибка: {exc}',
        'es': 'Se produjo un error durante el cálculo: {exc}',
        'zh': '计算时发生错误：{exc}',
        'hi': 'गणना के दौरान त्रुटि हुई: {exc}',
    },
    '位相を変えた一括計算は「軌道要素」または TLE で指定した衛星だけで使えます': {
        'en': 'The batch computation over phases works only for a satellite given by orbital elements or a TLE',
        'fr': 'Le calcul groupé sur les phases ne fonctionne que pour un satellite donné par éléments orbitaux ou TLE',
        'ru': 'Пакетный расчёт по фазам доступен только для спутника, заданного элементами орбиты или TLE',
        'es': 'El cálculo por lotes de fases solo funciona con un satélite dado por elementos orbitales o TLE',
        'zh': '改变相位的批量计算仅适用于以轨道根数或 TLE 指定的卫星',
        'hi': 'चरण बदलकर सामूहिक गणना केवल कक्षीय तत्वों या TLE से दिए गए उपग्रह के लिए है',
    },
    '位相の刻みは 5〜90° で指定してください': {
        'en': 'The phase step must be between 5° and 90°',
        'fr': 'Le pas de phase doit être compris entre 5° et 90°',
        'ru': 'Шаг по фазе должен быть от 5° до 90°',
        'es': 'El paso de fase debe estar entre 5° y 90°',
        'zh': '相位步长须为 5～90°',
        'hi': 'चरण का अंतराल 5° से 90° के बीच होना चाहिए',
    },
    '位相を変えた一括計算では期間を 1 年以内にしてください': {
        'en': 'For the batch computation over phases the period must be one year or less',
        'fr': 'Pour le calcul groupé sur les phases, la période doit être d’un an au plus',
        'ru': 'Для пакетного расчёта по фазам период не должен превышать 1 год',
        'es': 'Para el cálculo por lotes de fases el periodo debe ser de un año como máximo',
        'zh': '改变相位的批量计算，时间范围须在 1 年以内',
        'hi': 'चरण बदलकर सामूहिक गणना के लिए अवधि 1 वर्ष या उससे कम होनी चाहिए',
    },
    '保存したファイルに計算条件または現象の時刻がありません': {
        'en': 'The saved file has no computation settings or no time of the event',
        'fr': 'Le fichier enregistré ne contient pas les conditions de calcul ou l’heure de l’événement',
        'ru': 'В сохранённом файле нет условий расчёта или времени события',
        'es': 'El archivo guardado no contiene las condiciones de cálculo o la hora del evento',
        'zh': '保存的文件中没有计算条件或天象时刻',
        'hi': 'सहेजी गई फ़ाइल में गणना की शर्तें या घटना का समय नहीं है',
    },
    '保存した計算条件を解釈できません: {exc}': {
        'en': 'Cannot interpret the saved computation settings: {exc}',
        'fr': 'Conditions de calcul enregistrées incompréhensibles : {exc}',
        'ru': 'Не удаётся разобрать сохранённые условия расчёта: {exc}',
        'es': 'No se pueden interpretar las condiciones de cálculo guardadas: {exc}',
        'zh': '无法解析保存的计算条件：{exc}',
        'hi': 'सहेजी गई गणना की शर्तें समझ में नहीं आईं: {exc}',
    },
    'この現象を保存した計算条件で再計算できませんでした': {
        'en': 'This event could not be computed again with the saved settings',
        'fr': 'Impossible de recalculer cet événement avec les conditions enregistrées',
        'ru': 'Не удалось пересчитать это событие с сохранёнными условиями',
        'es': 'No se pudo recalcular este evento con las condiciones guardadas',
        'zh': '无法用保存的计算条件重新计算此天象',
        'hi': 'सहेजी गई शर्तों से इस घटना की दोबारा गणना नहीं हो सकी',
    },
    '地心（地球中心）': {
        'en': 'Geocentre (centre of the Earth)', 'fr': 'Géocentre (centre de la Terre)',
        'ru': 'Геоцентр (центр Земли)', 'es': 'Geocentro (centro de la Tierra)',
        'zh': '地心（地球中心）', 'hi': 'भूकेंद्र (पृथ्वी का केंद्र)',
    },
    '地図データの計算に失敗しました: {exc}': {
        'en': 'Failed to compute the map data: {exc}',
        'fr': 'Échec du calcul des données de la carte : {exc}',
        'ru': 'Не удалось рассчитать данные карты: {exc}',
        'es': 'Error al calcular los datos del mapa: {exc}',
        'zh': '地图数据计算失败：{exc}',
        'hi': 'मानचित्र डेटा की गणना विफल रही: {exc}',
    },
    'サーバーエラー: {exc}': {
        'en': 'Server error: {exc}', 'fr': 'Erreur du serveur : {exc}', 'ru': 'Ошибка сервера: {exc}',
        'es': 'Error del servidor: {exc}', 'zh': '服务器错误：{exc}', 'hi': 'सर्वर त्रुटि: {exc}',
    },
    # ---- context.py
    '暦ファイル {ephemeris} が {dir} にありません': {
        'en': 'The ephemeris file {ephemeris} is not in {dir}',
        'fr': 'Le fichier d’éphéméride {ephemeris} est absent de {dir}',
        'ru': 'Файл эфемерид {ephemeris} отсутствует в {dir}',
        'es': 'El archivo de efeméride {ephemeris} no está en {dir}',
        'zh': '{dir} 中没有历表文件 {ephemeris}',
        'hi': 'एफ़ेमेरिस फ़ाइल {ephemeris} {dir} में नहीं है',
    },
    # ---- local.py
    '指定期間には JPL Horizons の軌道データがありません': {
        'en': 'JPL Horizons has no orbit data for the given period',
        'fr': 'JPL Horizons n’a pas de données d’orbite pour cette période',
        'ru': 'В JPL Horizons нет данных об орбите за указанный период',
        'es': 'JPL Horizons no tiene datos de órbita para el periodo indicado',
        'zh': '指定期间内没有 JPL Horizons 的轨道数据',
        'hi': 'दी गई अवधि के लिए JPL Horizons में कक्षा डेटा नहीं है',
    },
    'JPL Horizons の軌道データがある期間（{start}〜{end}）に限定して計算しました': {
        'en': 'Computed only for the period with JPL Horizons orbit data ({start} – {end})',
        'fr': 'Calcul limité à la période couverte par les données d’orbite JPL Horizons ({start} – {end})',
        'ru': 'Расчёт ограничен периодом, для которого есть данные JPL Horizons ({start} – {end})',
        'es': 'Calculado solo para el periodo con datos de órbita de JPL Horizons ({start} – {end})',
        'zh': '仅在有 JPL Horizons 轨道数据的期间（{start}～{end}）内计算',
        'hi': 'केवल उस अवधि के लिए गणना की गई जिसमें JPL Horizons का कक्षा डेटा है ({start} – {end})',
    },
    '指定期間には NASA SSCWeb の軌道データがありません（提供期間 {period}）': {
        'en': 'NASA SSCWeb has no orbit data for the given period (available: {period})',
        'fr': 'NASA SSCWeb n’a pas de données d’orbite pour cette période (disponibles : {period})',
        'ru': 'В NASA SSCWeb нет данных об орбите за указанный период (доступно: {period})',
        'es': 'NASA SSCWeb no tiene datos de órbita para el periodo indicado (disponibles: {period})',
        'zh': '指定期间内没有 NASA SSCWeb 的轨道数据（提供期间 {period}）',
        'hi': 'दी गई अवधि के लिए NASA SSCWeb में कक्षा डेटा नहीं है (उपलब्ध: {period})',
    },
    'NASA SSCWeb の軌道データがある期間（{period}）に限定して計算しました': {
        'en': 'Computed only for the period with NASA SSCWeb orbit data ({period})',
        'fr': 'Calcul limité à la période couverte par les données d’orbite NASA SSCWeb ({period})',
        'ru': 'Расчёт ограничен периодом, для которого есть данные NASA SSCWeb ({period})',
        'es': 'Calculado solo para el periodo con datos de órbita de NASA SSCWeb ({period})',
        'zh': '仅在有 NASA SSCWeb 轨道数据的期间（{period}）内计算',
        'hi': 'केवल उस अवधि के लिए गणना की गई जिसमें NASA SSCWeb का कक्षा डेटा है ({period})',
    },
    '現象が多すぎるため最初の {n} 件のみ処理しました': {
        'en': 'Too many events: only the first {n} were processed',
        'fr': 'Trop d’événements : seuls les {n} premiers ont été traités',
        'ru': 'Слишком много событий: обработаны только первые {n}',
        'es': 'Demasiados eventos: solo se procesaron los primeros {n}',
        'zh': '天象过多，仅处理了前 {n} 个',
        'hi': 'बहुत अधिक घटनाएँ: केवल पहली {n} संसाधित की गईं',
    },
    # ---- observers.py
    '地心': {
        'en': 'Geocentre', 'fr': 'Géocentre', 'ru': 'Геоцентр', 'es': 'Geocentro', 'zh': '地心',
        'hi': 'भूकेंद्र',
    },
    '緯度は -90〜90 度で指定してください': {
        'en': 'The latitude must be between -90 and 90 degrees',
        'fr': 'La latitude doit être comprise entre -90 et 90 degrés',
        'ru': 'Широта должна быть от -90 до 90 градусов',
        'es': 'La latitud debe estar entre -90 y 90 grados',
        'zh': '纬度须在 -90～90 度之间',
        'hi': 'अक्षांश -90 से 90 डिग्री के बीच होना चाहिए',
    },
    '経度は -180〜180 度で指定してください': {
        'en': 'The longitude must be between -180 and 180 degrees',
        'fr': 'La longitude doit être comprise entre -180 et 180 degrés',
        'ru': 'Долгота должна быть от -180 до 180 градусов',
        'es': 'La longitud debe estar entre -180 y 180 grados',
        'zh': '经度须在 -180～180 度之间',
        'hi': 'देशांतर -180 से 180 डिग्री के बीच होना चाहिए',
    },
    '地球固定点 {lon:.2f}°': {
        'en': 'Earth-fixed point {lon:.2f}°', 'fr': 'Point fixe terrestre {lon:.2f}°',
        'ru': 'Точка, неподвижная относительно Земли, {lon:.2f}°', 'es': 'Punto fijo terrestre {lon:.2f}°',
        'zh': '地固点 {lon:.2f}°', 'hi': 'पृथ्वी-स्थिर बिंदु {lon:.2f}°',
    },
    'TLE の 1 行目は "1 "、2 行目は "2 " で始まる必要があります': {
        'en': 'Line 1 of a TLE must start with "1 " and line 2 with "2 "',
        'fr': 'La ligne 1 d’un TLE doit commencer par « 1 » et la ligne 2 par « 2 »',
        'ru': 'Строка 1 TLE должна начинаться с "1 ", строка 2 — с "2 "',
        'es': 'La línea 1 de un TLE debe empezar por "1 " y la línea 2 por "2 "',
        'zh': 'TLE 第 1 行须以 "1 " 开头，第 2 行须以 "2 " 开头',
        'hi': 'TLE की पंक्ति 1 "1 " से और पंक्ति 2 "2 " से शुरू होनी चाहिए',
    },
    'TLE を解釈できません: {exc}': {
        'en': 'Cannot interpret the TLE: {exc}', 'fr': 'TLE incompréhensible : {exc}',
        'ru': 'Не удаётся разобрать TLE: {exc}', 'es': 'No se puede interpretar el TLE: {exc}',
        'zh': '无法解析 TLE：{exc}', 'hi': 'TLE समझ में नहीं आया: {exc}',
    },
    'この TLE の近地点は地球内部にあります（再突入済みの可能性）': {
        'en': 'The perigee of this TLE is inside the Earth (the satellite may have re-entered)',
        'fr': 'Le périgée de ce TLE est à l’intérieur de la Terre (rentrée atmosphérique probable)',
        'ru': 'Перигей этого TLE находится внутри Земли (спутник, возможно, уже сошёл с орбиты)',
        'es': 'El perigeo de este TLE está dentro de la Tierra (el satélite pudo haber reentrado)',
        'zh': '此 TLE 的近地点在地球内部（可能已再入大气层）',
        'hi': 'इस TLE का उपभू पृथ्वी के भीतर है (उपग्रह शायद वायुमंडल में लौट चुका है)',
    },
    'TLE 元期から {age:.0f} 日離れています。SGP4 の位置誤差は数百 km 以上になり得るため、結果は目安として扱ってください。': {
        'en': '{age:.0f} days from the TLE epoch. The SGP4 position error can reach hundreds of km or more; treat the results as rough estimates.',
        'fr': 'À {age:.0f} jours de l’époque du TLE. L’erreur de position de SGP4 peut atteindre des centaines de km ou plus ; considérez les résultats comme approximatifs.',
        'ru': '{age:.0f} сут от эпохи TLE. Ошибка положения SGP4 может достигать сотен километров и более; считайте результаты ориентировочными.',
        'es': 'A {age:.0f} días de la época del TLE. El error de posición de SGP4 puede llegar a cientos de km o más; tome los resultados como aproximados.',
        'zh': '距 TLE 历元 {age:.0f} 天。SGP4 的位置误差可能达到数百千米以上，结果仅供参考。',
        'hi': 'TLE युग से {age:.0f} दिन दूर। SGP4 की स्थिति त्रुटि सैकड़ों किमी या अधिक हो सकती है; परिणामों को अनुमान मानें।',
    },
    'TLE 元期から {age:.0f} 日離れています（位置誤差は数 km〜数十 km 程度）。': {
        'en': '{age:.0f} days from the TLE epoch (position error of a few km to a few tens of km).',
        'fr': 'À {age:.0f} jours de l’époque du TLE (erreur de position de quelques km à quelques dizaines de km).',
        'ru': '{age:.0f} сут от эпохи TLE (ошибка положения от нескольких до нескольких десятков километров).',
        'es': 'A {age:.0f} días de la época del TLE (error de posición de unos pocos km a algunas decenas de km).',
        'zh': '距 TLE 历元 {age:.0f} 天（位置误差约数千米至数十千米）。',
        'hi': 'TLE युग से {age:.0f} दिन दूर (स्थिति त्रुटि कुछ किमी से कुछ दसियों किमी तक)।',
    },
    'TLE の 2 行目が短すぎます（69 文字の形式で指定してください）': {
        'en': 'Line 2 of the TLE is too short (it must be in the 69-character format)',
        'fr': 'La ligne 2 du TLE est trop courte (format de 69 caractères requis)',
        'ru': 'Строка 2 TLE слишком короткая (нужен формат из 69 символов)',
        'es': 'La línea 2 del TLE es demasiado corta (debe tener el formato de 69 caracteres)',
        'zh': 'TLE 第 2 行过短（请使用 69 个字符的格式）',
        'hi': 'TLE की पंक्ति 2 बहुत छोटी है (69 अक्षरों का प्रारूप चाहिए)',
    },
    'この軌道の大きさでは太陽同期軌道になりません（高度が高すぎます）': {
        'en': 'An orbit of this size cannot be sun-synchronous (the altitude is too high)',
        'fr': 'Une orbite de cette taille ne peut pas être héliosynchrone (altitude trop élevée)',
        'ru': 'Орбита такого размера не может быть солнечно-синхронной (слишком большая высота)',
        'es': 'Una órbita de este tamaño no puede ser heliosíncrona (altitud demasiado alta)',
        'zh': '此轨道尺寸无法成为太阳同步轨道（高度过高）',
        'hi': 'इस आकार की कक्षा सूर्य-समकालिक नहीं हो सकती (ऊँचाई बहुत अधिक है)',
    },
    '近地点高度が地表より低くなっています': {
        'en': 'The perigee altitude is below the surface of the Earth',
        'fr': 'L’altitude du périgée est sous la surface de la Terre',
        'ru': 'Высота перигея ниже поверхности Земли',
        'es': 'La altitud del perigeo está por debajo de la superficie terrestre',
        'zh': '近地点高度低于地表',
        'hi': 'उपभू की ऊँचाई पृथ्वी की सतह से नीचे है',
    },
    '離心率は 0 以上 1 未満で指定してください': {
        'en': 'The eccentricity must be at least 0 and less than 1',
        'fr': 'L’excentricité doit être supérieure ou égale à 0 et inférieure à 1',
        'ru': 'Эксцентриситет должен быть не меньше 0 и меньше 1',
        'es': 'La excentricidad debe ser mayor o igual que 0 y menor que 1',
        'zh': '离心率须大于等于 0 且小于 1',
        'hi': 'उत्केंद्रता 0 या अधिक और 1 से कम होनी चाहिए',
    },
    '軌道要素で指定した衛星': {
        'en': 'Satellite from orbital elements', 'fr': 'Satellite défini par éléments orbitaux',
        'ru': 'Спутник по элементам орбиты', 'es': 'Satélite por elementos orbitales',
        'zh': '由轨道根数指定的卫星', 'hi': 'कक्षीय तत्वों से दिया गया उपग्रह',
    },
    'JPL Horizons からデータを取得できませんでした:\n{msg}': {
        'en': 'Could not get data from JPL Horizons:\n{msg}',
        'fr': 'Impossible d’obtenir les données de JPL Horizons :\n{msg}',
        'ru': 'Не удалось получить данные JPL Horizons:\n{msg}',
        'es': 'No se pudieron obtener datos de JPL Horizons:\n{msg}',
        'zh': '无法从 JPL Horizons 获取数据：\n{msg}',
        'hi': 'JPL Horizons से डेटा नहीं मिल सका:\n{msg}',
    },
    'JPL Horizons の応答にデータ行がありません': {
        'en': 'The response of JPL Horizons has no data lines',
        'fr': 'La réponse de JPL Horizons ne contient aucune ligne de données',
        'ru': 'В ответе JPL Horizons нет строк с данными',
        'es': 'La respuesta de JPL Horizons no contiene líneas de datos',
        'zh': 'JPL Horizons 的响应中没有数据行',
        'hi': 'JPL Horizons के उत्तर में डेटा की कोई पंक्ति नहीं है',
    },
    'JPL Horizons の取得範囲外の時刻が要求されました': {
        'en': 'A time outside the data fetched from JPL Horizons was requested',
        'fr': 'Un instant hors des données obtenues de JPL Horizons a été demandé',
        'ru': 'Запрошено время вне диапазона данных, полученных из JPL Horizons',
        'es': 'Se pidió un instante fuera de los datos obtenidos de JPL Horizons',
        'zh': '请求了 JPL Horizons 获取范围以外的时刻',
        'hi': 'JPL Horizons से प्राप्त डेटा की सीमा से बाहर का समय माँगा गया',
    },
    'Horizons の天体 ID を指定してください（例: -170 = JWST）': {
        'en': 'Enter a Horizons body ID (e.g. -170 = JWST)',
        'fr': 'Indiquez un identifiant Horizons (ex. : -170 = JWST)',
        'ru': 'Укажите идентификатор объекта Horizons (например, -170 = JWST)',
        'es': 'Indique un ID de Horizons (p. ej., -170 = JWST)',
        'zh': '请指定 Horizons 天体 ID（例：-170 = JWST）',
        'hi': 'Horizons पिंड ID दर्ज करें (उदा. -170 = JWST)',
    },
    'Horizons から取得するデータ点が多すぎます（{n} 点）。期間を短くするか刻み幅を大きくしてください。': {
        'en': 'Too many data points to fetch from Horizons ({n}). Shorten the period or increase the step.',
        'fr': 'Trop de points à obtenir de Horizons ({n}). Raccourcissez la période ou augmentez le pas.',
        'ru': 'Слишком много точек для загрузки из Horizons ({n}). Сократите период или увеличьте шаг.',
        'es': 'Demasiados puntos que obtener de Horizons ({n}). Acorte el periodo o aumente el paso.',
        'zh': '从 Horizons 获取的数据点过多（{n} 个）。请缩短期间或增大步长。',
        'hi': 'Horizons से लेने के लिए बहुत अधिक डेटा बिंदु ({n})। अवधि छोटी करें या अंतराल बढ़ाएँ।',
    },
    'JPL Horizons から必要な期間の軌道データを取得できませんでした（探査機の軌道データが提供されている期間外の可能性があります）': {
        'en': 'Could not get orbit data for the needed period from JPL Horizons (it may be outside the period covered by the spacecraft data)',
        'fr': 'Impossible d’obtenir de JPL Horizons les données d’orbite de la période voulue (elle est peut-être hors de la période couverte)',
        'ru': 'Не удалось получить из JPL Horizons данные об орбите за нужный период (возможно, он вне периода, за который есть данные аппарата)',
        'es': 'No se pudieron obtener de JPL Horizons los datos de órbita del periodo necesario (puede estar fuera del periodo cubierto)',
        'zh': '无法从 JPL Horizons 获取所需期间的轨道数据（可能超出了探测器轨道数据的提供期间）',
        'hi': 'JPL Horizons से आवश्यक अवधि का कक्षा डेटा नहीं मिल सका (शायद यह यान के डेटा की अवधि से बाहर है)',
    },
    'NASA SSCWeb からデータを取得できませんでした: {msg}': {
        'en': 'Could not get data from NASA SSCWeb: {msg}',
        'fr': 'Impossible d’obtenir les données de NASA SSCWeb : {msg}',
        'ru': 'Не удалось получить данные NASA SSCWeb: {msg}',
        'es': 'No se pudieron obtener datos de NASA SSCWeb: {msg}',
        'zh': '无法从 NASA SSCWeb 获取数据：{msg}',
        'hi': 'NASA SSCWeb से डेटा नहीं मिल सका: {msg}',
    },
    'NASA SSCWeb に {sat} の {start}〜{end} の軌道データがありません': {
        'en': 'NASA SSCWeb has no orbit data of {sat} for {start} – {end}',
        'fr': 'NASA SSCWeb n’a pas de données d’orbite de {sat} pour {start} – {end}',
        'ru': 'В NASA SSCWeb нет данных об орбите {sat} за {start} – {end}',
        'es': 'NASA SSCWeb no tiene datos de órbita de {sat} para {start} – {end}',
        'zh': 'NASA SSCWeb 中没有 {sat} 在 {start}～{end} 的轨道数据',
        'hi': 'NASA SSCWeb में {sat} का {start} – {end} का कक्षा डेटा नहीं है',
    },
    'NASA SSCWeb の取得範囲外の時刻が要求されました': {
        'en': 'A time outside the data fetched from NASA SSCWeb was requested',
        'fr': 'Un instant hors des données obtenues de NASA SSCWeb a été demandé',
        'ru': 'Запрошено время вне диапазона данных, полученных из NASA SSCWeb',
        'es': 'Se pidió un instante fuera de los datos obtenidos de NASA SSCWeb',
        'zh': '请求了 NASA SSCWeb 获取范围以外的时刻',
        'hi': 'NASA SSCWeb से प्राप्त डेटा की सीमा से बाहर का समय माँगा गया',
    },
    'NASA SSCWeb の衛星一覧を取得できませんでした: {exc}': {
        'en': 'Could not get the list of satellites from NASA SSCWeb: {exc}',
        'fr': 'Impossible d’obtenir la liste des satellites de NASA SSCWeb : {exc}',
        'ru': 'Не удалось получить список спутников NASA SSCWeb: {exc}',
        'es': 'No se pudo obtener la lista de satélites de NASA SSCWeb: {exc}',
        'zh': '无法获取 NASA SSCWeb 的卫星列表：{exc}',
        'hi': 'NASA SSCWeb से उपग्रहों की सूची नहीं मिल सकी: {exc}',
    },
    'SSCWeb の衛星 ID を指定してください（例: hinode）': {
        'en': 'Enter an SSCWeb satellite ID (e.g. hinode)',
        'fr': 'Indiquez un identifiant de satellite SSCWeb (ex. : hinode)',
        'ru': 'Укажите идентификатор спутника SSCWeb (например, hinode)',
        'es': 'Indique un ID de satélite de SSCWeb (p. ej., hinode)',
        'zh': '请指定 SSCWeb 卫星 ID（例：hinode）',
        'hi': 'SSCWeb उपग्रह ID दर्ज करें (उदा. hinode)',
    },
    'NASA SSCWeb に衛星 ID「{sat}」はありません': {
        'en': 'NASA SSCWeb has no satellite with the ID "{sat}"',
        'fr': 'NASA SSCWeb n’a pas de satellite d’identifiant « {sat} »',
        'ru': 'В NASA SSCWeb нет спутника с идентификатором «{sat}»',
        'es': 'NASA SSCWeb no tiene ningún satélite con el ID «{sat}»',
        'zh': 'NASA SSCWeb 中没有卫星 ID“{sat}”',
        'hi': 'NASA SSCWeb में "{sat}" ID वाला कोई उपग्रह नहीं है',
    },
    'NASA SSCWeb から取得する軌道データが多すぎます（{days:.0f} 日分）。期間を短くしてください': {
        'en': 'Too much orbit data to fetch from NASA SSCWeb ({days:.0f} days). Shorten the period',
        'fr': 'Trop de données d’orbite à obtenir de NASA SSCWeb ({days:.0f} jours). Raccourcissez la période',
        'ru': 'Слишком много данных об орбите для загрузки из NASA SSCWeb ({days:.0f} сут). Сократите период',
        'es': 'Demasiados datos de órbita que obtener de NASA SSCWeb ({days:.0f} días). Acorte el periodo',
        'zh': '从 NASA SSCWeb 获取的轨道数据过多（{days:.0f} 天）。请缩短期间',
        'hi': 'NASA SSCWeb से लेने के लिए बहुत अधिक कक्षा डेटा ({days:.0f} दिन)। अवधि छोटी करें',
    },
    'NASA SSCWeb から必要な期間の軌道データを取得できませんでした（欠損または提供期間外の可能性があります）': {
        'en': 'Could not get orbit data for the needed period from NASA SSCWeb (data may be missing or outside the available period)',
        'fr': 'Impossible d’obtenir de NASA SSCWeb les données d’orbite de la période voulue (données manquantes ou hors période)',
        'ru': 'Не удалось получить из NASA SSCWeb данные об орбите за нужный период (данные отсутствуют или вне доступного периода)',
        'es': 'No se pudieron obtener de NASA SSCWeb los datos de órbita del periodo necesario (faltan datos o está fuera del periodo disponible)',
        'zh': '无法从 NASA SSCWeb 获取所需期间的轨道数据（可能缺失或超出提供期间）',
        'hi': 'NASA SSCWeb से आवश्यक अवधि का कक्षा डेटा नहीं मिल सका (डेटा अनुपलब्ध या अवधि से बाहर हो सकता है)',
    },
    'CelesTrak に NORAD {norad} の軌道要素が見つかりません': {
        'en': 'CelesTrak has no orbital elements for NORAD {norad}',
        'fr': 'CelesTrak n’a pas d’éléments orbitaux pour NORAD {norad}',
        'ru': 'В CelesTrak нет элементов орбиты для NORAD {norad}',
        'es': 'CelesTrak no tiene elementos orbitales de NORAD {norad}',
        'zh': 'CelesTrak 中找不到 NORAD {norad} 的轨道根数',
        'hi': 'CelesTrak में NORAD {norad} के कक्षीय तत्व नहीं मिले',
    },
    '静止衛星 {lon:.1f}°': {
        'en': 'Geostationary satellite {lon:.1f}°', 'fr': 'Satellite géostationnaire {lon:.1f}°',
        'ru': 'Геостационарный спутник {lon:.1f}°', 'es': 'Satélite geoestacionario {lon:.1f}°',
        'zh': '静止卫星 {lon:.1f}°', 'hi': 'भूस्थिर उपग्रह {lon:.1f}°',
    },
    '不明な観測者タイプです: {kind}': {
        'en': 'Unknown observer type: {kind}', 'fr': 'Type d’observateur inconnu : {kind}',
        'ru': 'Неизвестный тип наблюдателя: {kind}', 'es': 'Tipo de observador desconocido: {kind}',
        'zh': '未知的观测者类型：{kind}', 'hi': 'अज्ञात प्रेक्षक प्रकार: {kind}',
    },
    # ---- net.py
    'ダウンロードが途中で切れました（{done} / {total} バイト）': {
        'en': 'The download was cut off ({done} / {total} bytes)',
        'fr': 'Le téléchargement a été interrompu ({done} / {total} octets)',
        'ru': 'Загрузка прервалась ({done} / {total} байт)',
        'es': 'La descarga se interrumpió ({done} / {total} bytes)',
        'zh': '下载中断（{done} / {total} 字节）',
        'hi': 'डाउनलोड बीच में रुक गया ({done} / {total} बाइट)',
    },
}
