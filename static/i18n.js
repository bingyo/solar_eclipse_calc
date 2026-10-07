/* 日食・太陽面通過 精密計算機 - languages of the page
 *
 * The Japanese text is the message itself and the key of its translations, given in the
 * order of LANGS after Japanese (en, fr, ru, es, zh, hi).  {name} fields are filled in by t().
 * Static text of index.html is translated in place (translatePage); the parts with markup
 * (data-i18n-html) have their own HTML in HTML_BLOCKS.
 */
'use strict';

const LANGS = [
  ['ja', '日本語', 'ja-JP'], ['en', 'English', 'en-GB'], ['fr', 'Français', 'fr-FR'], ['ru', 'Русский', 'ru-RU'],
  ['es', 'Español', 'es-ES'], ['zh', '中文', 'zh-CN'], ['hi', 'हिन्दी', 'hi-IN'],
];
const LANG_INDEX = { en: 0, fr: 1, ru: 2, es: 3, zh: 4, hi: 5 };

function knownLang(code) { return LANGS.some(([c]) => c === code); }
/* The language chosen before, or the first one of the browser that the page has (else English). */
function initialLang() {
  try {
    const v = localStorage.getItem('lang');
    if (knownLang(v)) return v;
  } catch (_) { /* ignore */ }
  for (const l of navigator.languages || [navigator.language || '']) {
    const code = String(l).toLowerCase().split('-')[0];
    if (knownLang(code)) return code;
  }
  return 'en';
}
let LANG = initialLang();
document.documentElement.lang = LANG;

function tIn(lang, key, vars) {
  let s = key;
  if (lang !== 'ja') {
    const e = I18N[key];
    if (e) s = e[LANG_INDEX[lang]] || e[0] || key;
  }
  return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m)) : s;
}
/* `key` (Japanese) in the language of the page. */
function t(key, vars) { return tIn(LANG, key, vars); }
function langLocale() { return LANGS.find(([c]) => c === LANG)[2]; }
function wideScript() { return LANG === 'ja' || LANG === 'zh'; }
/* Separators and parentheses: 「A・B」「A、B」「名前（説明）」 in Japanese, "A, B" and "name (note)" elsewhere. */
function sep() { return LANG === 'ja' ? '・' : LANG === 'zh' ? '，' : ', '; }
function listSep() { return wideScript() ? '、' : ', '; }
function paren(s) { return wideScript() ? `（${s}）` : ` (${s})`; }
function span(a, b) { return LANG === 'ja' ? `${a} 〜 ${b}` : LANG === 'zh' ? `${a} ～ ${b}` : `${a} – ${b}`; }
function sentences(list) { return list.filter(Boolean).join(wideScript() ? '' : ' '); }
function compassPoints() { return COMPASS[LANG]; }

const JP_TEXT = /[\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]/;   // kana, kanji, full-width forms
let _static = null;
/* Translate the static text of index.html (remembering the Japanese the first time). */
function translatePage() {
  if (!_static) {
    _static = { text: [], attr: [], html: [], title: document.title };
    const walk = (node) => {
      for (const c of node.childNodes) {
        if (c.nodeType === Node.TEXT_NODE) {
          if (JP_TEXT.test(c.nodeValue)) _static.text.push([c, c.nodeValue]);
          continue;
        }
        if (c.nodeType !== Node.ELEMENT_NODE || c.tagName === 'SCRIPT' || c.tagName === 'STYLE') continue;
        for (const a of ['title', 'placeholder', 'aria-label']) {
          const v = c.getAttribute(a);
          if (v && JP_TEXT.test(v)) _static.attr.push([c, a, v]);
        }
        if (c.dataset.i18nHtml) _static.html.push([c, c.innerHTML]);
        else walk(c);
      }
    };
    walk(document.body);
  }
  const key = (s) => s.replace(/\s+/g, ' ').trim();
  for (const [node, ja] of _static.text) {
    const [, lead, body, tail] = ja.match(/^(\s*)([\s\S]*?)(\s*)$/);
    node.nodeValue = LANG === 'ja' ? ja : lead + t(key(body)) + tail;
  }
  for (const [e, a, ja] of _static.attr) e.setAttribute(a, LANG === 'ja' ? ja : t(key(ja)));
  for (const [e, ja] of _static.html) {
    const h = HTML_BLOCKS[e.dataset.i18nHtml];
    e.innerHTML = LANG === 'ja' || !h ? ja : h[LANG] || h.en;
  }
  document.title = t(_static.title);
  document.documentElement.lang = LANG;
}
function setLanguage(lang) {
  LANG = knownLang(lang) ? lang : 'en';
  try { localStorage.setItem('lang', LANG); } catch (_) { /* ignore */ }
  translatePage();
}

/* 16 points of the compass from north through east. */
const COMPASS = {
  ja: ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東', '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西'],
  en: ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'],
  fr: ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'],
  ru: ['С', 'ССВ', 'СВ', 'ВСВ', 'В', 'ВЮВ', 'ЮВ', 'ЮЮВ', 'Ю', 'ЮЮЗ', 'ЮЗ', 'ЗЮЗ', 'З', 'ЗСЗ', 'СЗ', 'ССЗ'],
  es: ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSO', 'SO', 'OSO', 'O', 'ONO', 'NO', 'NNO'],
  zh: ['北', '北东北', '东北', '东东北', '东', '东东南', '东南', '南东南', '南', '南西南', '西南', '西西南', '西', '西西北', '西北', '北西北'],
  hi: ['उ', 'उउपू', 'उपू', 'पूउपू', 'पू', 'पूदपू', 'दपू', 'ददपू', 'द', 'ददप', 'दप', 'पदप', 'प', 'पउप', 'उप', 'उउप'],
};

/* Messages: "日本語": [English, Français, Русский, Español, 中文, हिन्दी] */
const I18N = /*BEGIN-MESSAGES*/{
"{s}秒": ["{s} s", "{s} s", "{s} с", "{s} s", "{s} 秒", "{s} से."],
"{m}分{s}秒": ["{m} min {s} s", "{m} min {s} s", "{m} мин {s} с", "{m} min {s} s", "{m} 分 {s} 秒", "{m} मि. {s} से."],
"{h}時間{m}分": ["{h} h {m} min", "{h} h {m} min", "{h} ч {m} мин", "{h} h {m} min", "{h} 小时 {m} 分", "{h} घं. {m} मि."],
"北緯 {v}°": ["{v}° N", "{v}° N", "{v}° с. ш.", "{v}° N", "北纬 {v}°", "{v}° उ."],
"南緯 {v}°": ["{v}° S", "{v}° S", "{v}° ю. ш.", "{v}° S", "南纬 {v}°", "{v}° द."],
"東経 {v}°": ["{v}° E", "{v}° E", "{v}° в. д.", "{v}° E", "东经 {v}°", "{v}° पू."],
"西経 {v}°": ["{v}° W", "{v}° O", "{v}° з. д.", "{v}° O", "西经 {v}°", "{v}° प."],
"皆既日食": ["Total solar eclipse", "Éclipse totale de Soleil", "Полное солнечное затмение", "Eclipse total de Sol", "日全食", "पूर्ण सूर्य ग्रहण"],
"金環日食": ["Annular solar eclipse", "Éclipse annulaire de Soleil", "Кольцеобразное солнечное затмение", "Eclipse anular de Sol", "日环食", "वलयाकार सूर्य ग्रहण"],
"金環皆既日食": ["Hybrid solar eclipse", "Éclipse hybride de Soleil", "Гибридное солнечное затмение", "Eclipse híbrido de Sol", "全环食", "संकर सूर्य ग्रहण"],
"部分日食": ["Partial solar eclipse", "Éclipse partielle de Soleil", "Частное солнечное затмение", "Eclipse parcial de Sol", "日偏食", "आंशिक सूर्य ग्रहण"],
"水星の太陽面通過": ["Transit of Mercury", "Transit de Mercure", "Прохождение Меркурия по диску Солнца", "Tránsito de Mercurio", "水星凌日", "बुध का पारगमन"],
"金星の太陽面通過": ["Transit of Venus", "Transit de Vénus", "Прохождение Венеры по диску Солнца", "Tránsito de Venus", "金星凌日", "शुक्र का पारगमन"],
"（外接のみ）": [" (external contacts only)", " (contacts extérieurs seulement)", " (только внешние контакты)", " (solo contactos externos)", "（仅外切）", " (केवल बाह्य स्पर्श)"],
"（非中心）": [" (non-central)", " (non central)", " (нецентральное)", " (no central)", "（非中心食）", " (अकेंद्रीय)"],
"第1接触": ["1st contact", "1er contact", "1-й контакт", "1.er contacto", "第一接触", "प्रथम स्पर्श"],
"欠け始め": ["eclipse begins", "début de l’éclipse", "начало затмения", "inicio del eclipse", "初亏", "ग्रहण आरंभ"],
"第2接触": ["2nd contact", "2e contact", "2-й контакт", "2.º contacto", "第二接触", "द्वितीय स्पर्श"],
"皆既の始まり": ["totality begins", "début de la totalité", "начало полной фазы", "inicio de la totalidad", "食既（全食开始）", "पूर्णता आरंभ"],
"金環の始まり": ["annularity begins", "début de l’annularité", "начало кольцеобразной фазы", "inicio de la anularidad", "食既（环食开始）", "वलय आरंभ"],
"中心食の始まり": ["central eclipse begins", "début de l’éclipse centrale", "начало центральной фазы", "inicio del eclipse central", "中心食开始", "केंद्रीय ग्रहण आरंभ"],
"食の最大": ["Maximum eclipse", "Maximum de l’éclipse", "Максимум затмения", "Máximo del eclipse", "食甚", "अधिकतम ग्रहण"],
"第3接触": ["3rd contact", "3e contact", "3-й контакт", "3.er contacto", "第三接触", "तृतीय स्पर्श"],
"皆既の終わり": ["totality ends", "fin de la totalité", "конец полной фазы", "fin de la totalidad", "生光（全食结束）", "पूर्णता समाप्त"],
"金環の終わり": ["annularity ends", "fin de l’annularité", "конец кольцеобразной фазы", "fin de la anularidad", "生光（环食结束）", "वलय समाप्त"],
"中心食の終わり": ["central eclipse ends", "fin de l’éclipse centrale", "конец центральной фазы", "fin del eclipse central", "中心食结束", "केंद्रीय ग्रहण समाप्त"],
"第4接触": ["4th contact", "4e contact", "4-й контакт", "4.º contacto", "第四接触", "चतुर्थ स्पर्श"],
"欠け終わり": ["eclipse ends", "fin de l’éclipse", "конец затмения", "fin del eclipse", "复圆", "ग्रहण समाप्त"],
"外接・入り始め": ["external, ingress begins", "contact extérieur, début de l’entrée", "внешний, начало входа", "externo, inicio de la entrada", "外切，凌始", "बाह्य, प्रवेश आरंभ"],
"内接・入り終わり": ["internal, ingress ends", "contact intérieur, fin de l’entrée", "внутренний, конец входа", "interno, fin de la entrada", "内切，完全进入", "आंतरिक, प्रवेश पूर्ण"],
"最大": ["Maximum", "Maximum", "Максимум", "Máximo", "最大", "अधिकतम"],
"太陽中心に最も近づく": ["closest to the centre of the Sun", "au plus près du centre du Soleil", "ближе всего к центру Солнца", "más cerca del centro del Sol", "最接近日面中心", "सूर्य के केंद्र के सबसे निकट"],
"内接・出始め": ["internal, egress begins", "contact intérieur, début de la sortie", "внутренний, начало выхода", "interno, inicio de la salida", "内切，开始离开", "आंतरिक, निर्गमन आरंभ"],
"外接・出終わり": ["external, egress ends", "contact extérieur, fin de la sortie", "внешний, конец выхода", "externo, fin de la salida", "外切，凌终", "बाह्य, निर्गमन पूर्ण"],
"月": ["the Moon", "la Lune", "Луна", "la Luna", "月球", "चंद्रमा"],
"水星": ["Mercury", "Mercure", "Меркурий", "Mercurio", "水星", "बुध"],
"金星": ["Venus", "Vénus", "Венера", "Venus", "金星", "शुक्र"],
"サーバーに接続できません: {msg}": ["Cannot connect to the server: {msg}", "Connexion au serveur impossible : {msg}", "Нет связи с сервером: {msg}", "No se puede conectar con el servidor: {msg}", "无法连接服务器：{msg}", "सर्वर से कनेक्ट नहीं हो सका: {msg}"],
"東京": ["Tokyo", "Tokyo", "Токио", "Tokio", "东京", "टोक्यो"],
"結果をテキストでコピー": ["Copy results as text", "Copier les résultats en texte", "Копировать результаты как текст", "Copiar resultados como texto", "以文本复制结果", "परिणाम टेक्स्ट के रूप में कॉपी करें"],
"日食計算機は終了しています": ["The eclipse calculator has stopped", "Le calculateur d’éclipses est arrêté", "Калькулятор затмений остановлен", "La calculadora de eclipses se ha detenido", "日食计算器已关闭", "ग्रहण कैलकुलेटर बंद हो चुका है"],
"「日食計算機」アプリを開いてください": ["open the “日食計算機” (eclipse calculator) app", "ouvrez l’application « 日食計算機 » (calculateur d’éclipses)", "откройте приложение «日食計算機» (калькулятор затмений)", "abra la aplicación «日食計算機» (calculadora de eclipses)", "请打开“日食計算機”（日食计算器）应用", "“日食計算機” (ग्रहण कैलकुलेटर) ऐप खोलें"],
"start.bat（Mac・Linux は start.command）を起動してください": ["run start.bat (start.command on Mac and Linux)", "lancez start.bat (start.command sur Mac et Linux)", "запустите start.bat (на Mac и Linux — start.command)", "ejecute start.bat (start.command en Mac y Linux)", "请运行 start.bat（Mac、Linux 上为 start.command）", "start.bat चलाएँ (Mac और Linux पर start.command)"],
"このタブは閉じてかまいません。もう一度使うときは{again}。": ["You can close this tab. To use it again, {again}.", "Vous pouvez fermer cet onglet. Pour l’utiliser à nouveau, {again}.", "Эту вкладку можно закрыть. Чтобы снова начать работу, {again}.", "Puede cerrar esta pestaña. Para volver a usarla, {again}.", "可以关闭此标签页。再次使用时，{again}。", "आप यह टैब बंद कर सकते हैं। फिर से उपयोग करने के लिए {again}।"],
"日食計算機を終了しますか？": ["Quit the eclipse calculator?", "Quitter le calculateur d’éclipses ?", "Завершить работу калькулятора затмений?", "¿Cerrar la calculadora de eclipses?", "要关闭日食计算器吗？", "ग्रहण कैलकुलेटर बंद करें?"],
"日食計算機を終了しました": ["The eclipse calculator has been closed", "Le calculateur d’éclipses a été fermé", "Калькулятор затмений закрыт", "La calculadora de eclipses se ha cerrado", "日食计算器已关闭", "ग्रहण कैलकुलेटर बंद कर दिया गया"],
"（1849〜2150年）": [" (1849–2150)", " (1849–2150)", " (1849–2150)", " (1849–2150)", "（1849～2150 年）", " (1849–2150)"],
"（1550〜2650年）": [" (1550–2650)", " (1550–2650)", " (1550–2650)", " (1550–2650)", "（1550～2650 年）", " (1550–2650)"],
"{name} を追加（{desc}）": ["Add {name} ({desc})", "Ajouter {name} ({desc})", "Добавить {name} ({desc})", "Añadir {name} ({desc})", "添加 {name}（{desc}）", "{name} जोड़ें ({desc})"],
"1849〜2150年・約 32 MB": ["1849–2150, about 32 MB", "1849–2150, environ 32 Mo", "1849–2150 гг., около 32 МБ", "1849–2150, unos 32 MB", "1849～2150 年，约 32 MB", "1849–2150, लगभग 32 MB"],
"1550〜2650年・約 114 MB": ["1550–2650, about 114 MB", "1550–2650, environ 114 Mo", "1550–2650 гг., около 114 МБ", "1550–2650, unos 114 MB", "1550～2650 年，约 114 MB", "1550–2650, लगभग 114 MB"],
"JPL から {name} をダウンロード中…（数分かかることがあります）": ["Downloading {name} from JPL… (this may take a few minutes)", "Téléchargement de {name} depuis le JPL… (cela peut prendre quelques minutes)", "Загрузка {name} из JPL… (может занять несколько минут)", "Descargando {name} del JPL… (puede tardar unos minutos)", "正在从 JPL 下载 {name}…（可能需要几分钟）", "JPL से {name} डाउनलोड हो रहा है… (कुछ मिनट लग सकते हैं)"],
"{name} を追加し、暦として選択しました。": ["{name} was added and selected as the ephemeris.", "{name} a été ajouté et choisi comme éphéméride.", "{name} добавлен и выбран в качестве эфемерид.", "{name} se añadió y se seleccionó como efeméride.", "已添加 {name} 并选为历表。", "{name} जोड़ा गया और एफ़ेमेरिस के रूप में चुना गया।"],
"選択中の暦で計算できる期間: {start} 〜 {end}": ["Period covered by the selected ephemeris: {start} – {end}", "Période couverte par l’éphéméride choisie : {start} – {end}", "Период, охватываемый выбранными эфемеридами: {start} – {end}", "Periodo cubierto por la efeméride elegida: {start} – {end}", "所选历表可计算的期间：{start} ～ {end}", "चुने गए एफ़ेमेरिस की अवधि: {start} – {end}"],
"NASA SSCWeb の衛星一覧を取得中…": ["Getting the list of satellites from NASA SSCWeb…", "Récupération de la liste des satellites de NASA SSCWeb…", "Получение списка спутников NASA SSCWeb…", "Obteniendo la lista de satélites de NASA SSCWeb…", "正在获取 NASA SSCWeb 的卫星列表…", "NASA SSCWeb से उपग्रहों की सूची ली जा रही है…"],
"「{id}」は SSCWeb の衛星一覧にありません": ["“{id}” is not in the list of SSCWeb satellites", "« {id} » n’est pas dans la liste des satellites SSCWeb", "«{id}» нет в списке спутников SSCWeb", "«{id}» no está en la lista de satélites de SSCWeb", "SSCWeb 卫星列表中没有“{id}”", "“{id}” SSCWeb उपग्रह सूची में नहीं है"],
"軌道データ: {start} 〜 {end}（{res} 秒間隔）": ["Orbit data: {start} – {end} (every {res} s)", "Données d’orbite : {start} – {end} (toutes les {res} s)", "Данные об орбите: {start} – {end} (шаг {res} с)", "Datos de órbita: {start} – {end} (cada {res} s)", "轨道数据：{start} ～ {end}（间隔 {res} 秒）", "कक्षा डेटा: {start} – {end} (हर {res} से.)"],
"昇交点の地方時を入力してください（例: 18:00）": ["Enter the local time of the ascending node (e.g. 18:00)", "Saisissez l’heure locale du nœud ascendant (ex. : 18:00)", "Введите местное время восходящего узла (например, 18:00)", "Introduzca la hora local del nodo ascendente (p. ej., 18:00)", "请输入升交点地方时（例：18:00）", "आरोही पात का स्थानीय समय दर्ज करें (उदा. 18:00)"],
"この高度では太陽同期軌道になりません": ["No sun-synchronous orbit at this altitude", "Pas d’orbite héliosynchrone à cette altitude", "На этой высоте солнечно-синхронная орбита невозможна", "No hay órbita heliosíncrona a esta altitud", "此高度无法形成太阳同步轨道", "इस ऊँचाई पर सूर्य-समकालिक कक्षा संभव नहीं"],
"太陽同期になるよう自動で決めた値": ["Set automatically for a sun-synchronous orbit", "Calculée automatiquement pour une orbite héliosynchrone", "Вычислено автоматически для солнечно-синхронной орбиты", "Calculada automáticamente para una órbita heliosíncrona", "为太阳同步轨道自动确定的值", "सूर्य-समकालिक कक्षा के लिए अपने-आप निर्धारित"],
"高度 {lo}〜{hi} km": ["altitude {lo}–{hi} km", "altitude {lo}–{hi} km", "высота {lo}–{hi} км", "altitud {lo}–{hi} km", "高度 {lo}～{hi} km", "ऊँचाई {lo}–{hi} किमी"],
"周期 {p} 分": ["period {p} min", "période {p} min", "период {p} мин", "periodo {p} min", "周期 {p} 分钟", "आवर्तकाल {p} मि."],
"CelesTrak から取得中…": ["Getting from CelesTrak…", "Récupération depuis CelesTrak…", "Получение данных из CelesTrak…", "Obteniendo de CelesTrak…", "正在从 CelesTrak 获取…", "CelesTrak से लिया जा रहा है…"],
"元期: {epoch}": ["Epoch: {epoch}", "Époque : {epoch}", "Эпоха: {epoch}", "Época: {epoch}", "历元：{epoch}", "युग: {epoch}"],
"近地点 {v} km": ["perigee {v} km", "périgée {v} km", "перигей {v} км", "perigeo {v} km", "近地点 {v} km", "उपभू {v} किमी"],
"遠地点 {v} km": ["apogee {v} km", "apogée {v} km", "апогей {v} км", "apogeo {v} km", "远地点 {v} km", "अपभू {v} किमी"],
"TLE の予報精度は元期の前後数日が目安です。": ["A TLE predicts well only for a few days around its epoch.", "Un TLE n’est précis que quelques jours autour de son époque.", "TLE даёт хороший прогноз лишь на несколько суток вокруг эпохи.", "Un TLE solo predice bien unos días alrededor de su época.", "TLE 的预报精度以历元前后几天为宜。", "TLE केवल अपने युग के आसपास कुछ दिनों तक ही सटीक रहता है।"],
"地図で選んだ地点": ["Place picked on the map", "Lieu choisi sur la carte", "Точка, выбранная на карте", "Lugar elegido en el mapa", "在地图上选择的地点", "मानचित्र पर चुना गया स्थान"],
"TLE の 1 行目（\"1 \"で始まる）と 2 行目（\"2 \"で始まる）を貼り付けてください": ["Paste line 1 (starting with \"1 \") and line 2 (starting with \"2 \") of the TLE", "Collez la ligne 1 (commençant par « 1 ») et la ligne 2 (commençant par « 2 ») du TLE", "Вставьте строку 1 (начинается с \"1 \") и строку 2 (начинается с \"2 \") TLE", "Pegue la línea 1 (empieza por \"1 \") y la línea 2 (empieza por \"2 \") del TLE", "请粘贴 TLE 的第 1 行（以 \"1 \" 开头）和第 2 行（以 \"2 \" 开头）", "TLE की पंक्ति 1 (\"1 \" से शुरू) और पंक्ति 2 (\"2 \" से शुरू) चिपकाएँ"],
"軌道要素の元期を入力してください": ["Enter the epoch of the orbital elements", "Saisissez l’époque des éléments orbitaux", "Введите эпоху элементов орбиты", "Introduzca la época de los elementos orbitales", "请输入轨道根数的历元", "कक्षीय तत्वों का युग दर्ज करें"],
"軌道長半径を入力してください": ["Enter the semi-major axis", "Saisissez le demi-grand axe", "Введите большую полуось", "Introduzca el semieje mayor", "请输入半长轴", "अर्ध-दीर्घ अक्ष दर्ज करें"],
"軌道離心率は 0 以上 1 未満で入力してください": ["The eccentricity must be at least 0 and less than 1", "L’excentricité doit être ≥ 0 et < 1", "Эксцентриситет должен быть не меньше 0 и меньше 1", "La excentricidad debe ser ≥ 0 y < 1", "偏心率须大于等于 0 且小于 1", "उत्केंद्रता 0 या अधिक और 1 से कम होनी चाहिए"],
"NASA SSCWeb の衛星 ID を入力してください（例: hinode）": ["Enter a NASA SSCWeb satellite ID (e.g. hinode)", "Saisissez un identifiant de satellite NASA SSCWeb (ex. : hinode)", "Введите идентификатор спутника NASA SSCWeb (например, hinode)", "Introduzca un ID de satélite de NASA SSCWeb (p. ej., hinode)", "请输入 NASA SSCWeb 卫星 ID（例：hinode）", "NASA SSCWeb उपग्रह ID दर्ज करें (उदा. hinode)"],
"計算する現象を 1 つ以上選んでください": ["Select at least one phenomenon to compute", "Choisissez au moins un phénomène à calculer", "Выберите хотя бы одно явление для расчёта", "Seleccione al menos un fenómeno para calcular", "请至少选择一种要计算的天象", "गणना के लिए कम से कम एक घटना चुनें"],
"JPL Horizons から軌道を取得して計算中…": ["Getting the orbit from JPL Horizons and computing…", "Récupération de l’orbite depuis JPL Horizons et calcul…", "Получение орбиты из JPL Horizons и расчёт…", "Obteniendo la órbita de JPL Horizons y calculando…", "正在从 JPL Horizons 获取轨道并计算…", "JPL Horizons से कक्षा लेकर गणना हो रही है…"],
"NASA SSCWeb から軌道を取得して計算中…": ["Getting the orbit from NASA SSCWeb and computing…", "Récupération de l’orbite depuis NASA SSCWeb et calcul…", "Получение орбиты из NASA SSCWeb и расчёт…", "Obteniendo la órbita de NASA SSCWeb y calculando…", "正在从 NASA SSCWeb 获取轨道并计算…", "NASA SSCWeb से कक्षा लेकर गणना हो रही है…"],
"CelesTrak から TLE を取得して計算中…": ["Getting the TLE from CelesTrak and computing…", "Récupération du TLE depuis CelesTrak et calcul…", "Получение TLE из CelesTrak и расчёт…", "Obteniendo el TLE de CelesTrak y calculando…", "正在从 CelesTrak 获取 TLE 并计算…", "CelesTrak से TLE लेकर गणना हो रही है…"],
"計算中…": ["Computing…", "Calcul en cours…", "Идёт расчёт…", "Calculando…", "正在计算…", "गणना हो रही है…"],
"平均近点角を {step}° ずつ変えて計算中…": ["Computing for mean anomalies {step}° apart…", "Calcul pour des anomalies moyennes espacées de {step}°…", "Расчёт для средних аномалий с шагом {step}°…", "Calculando para anomalías medias separadas {step}°…", "正在以 {step}° 为步长改变平近点角进行计算…", "{step}° के अंतराल पर माध्य असंगति बदलकर गणना हो रही है…"],
"完了（{s} 秒）": ["Done ({s} s)", "Terminé ({s} s)", "Готово ({s} с)", "Listo ({s} s)", "完成（{s} 秒）", "पूर्ण ({s} से.)"],
"地球全体": ["Whole Earth", "Terre entière", "Вся Земля", "Toda la Tierra", "全球", "पूरी पृथ्वी"],
"標高 {v} m": ["elevation {v} m", "altitude {v} m", "высота над уровнем моря {v} м", "altitud {v} m", "海拔 {v} m", "ऊँचाई {v} मी"],
"地球中心": ["Centre of the Earth", "Centre de la Terre", "Центр Земли", "Centro de la Tierra", "地心", "पृथ्वी का केंद्र"],
"軌道要素": ["Orbital elements", "Éléments orbitaux", "Элементы орбиты", "Elementos orbitales", "轨道根数", "कक्षीय तत्व"],
"地球固定位置": ["Earth-fixed position", "Position fixe par rapport à la Terre", "Неподвижно относительно Земли", "Posición fija respecto a la Tierra", "地固位置", "पृथ्वी-स्थिर स्थिति"],
"{v} km": ["{v} km", "{v} km", "{v} км", "{v} km", "{v} km", "{v} किमी"],
"高度 {v} km": ["altitude {v} km", "altitude {v} km", "высота {v} км", "altitud {v} km", "高度 {v} km", "ऊँचाई {v} किमी"],
"傾斜角 {v}°": ["inclination {v}°", "inclinaison {v}°", "наклонение {v}°", "inclinación {v}°", "倾角 {v}°", "झुकाव {v}°"],
"昇交点赤経 {v}°": ["RAAN {v}°", "ascension droite du nœud ascendant {v}°", "прямое восхождение восходящего узла {v}°", "ascensión recta del nodo ascendente {v}°", "升交点赤经 {v}°", "आरोही पात का विषुवांश {v}°"],
"昇交点の地方時 {v}": ["local time of ascending node {v}", "heure locale du nœud ascendant {v}", "местное время восходящего узла {v}", "hora local del nodo ascendente {v}", "升交点地方时 {v}", "आरोही पात का स्थानीय समय {v}"],
"太陽同期": ["sun-synchronous", "héliosynchrone", "солнечно-синхронная", "heliosíncrona", "太阳同步", "सूर्य-समकालिक"],
"元期 {v}": ["epoch {v}", "époque {v}", "эпоха {v}", "época {v}", "历元 {v}", "युग {v}"],
"<span class=\"big\">{n} 件</span> の現象が見つかりました": ["<span class=\"big\">{n}</span> events found", "<span class=\"big\">{n}</span> événement(s) trouvé(s)", "Найдено явлений: <span class=\"big\">{n}</span>", "<span class=\"big\">{n}</span> eventos encontrados", "找到 <span class=\"big\">{n} 个</span>天象", "<span class=\"big\">{n}</span> घटनाएँ मिलीं"],
"（うち見えるもの {n} 件）": [" ({n} of them visible)", " (dont {n} visible(s))", " (из них видимых: {n})", " ({n} de ellos visibles)", "（其中可见 {n} 个）", " (इनमें से {n} दिखाई देंगी)"],
"観測者: {obs}": ["Observer: {obs}", "Observateur : {obs}", "Наблюдатель: {obs}", "Observador: {obs}", "观测者：{obs}", "प्रेक्षक: {obs}"],
"期間: {start} 〜 {end}（UTC）": ["Period: {start} – {end} (UTC)", "Période : {start} – {end} (UTC)", "Период: {start} – {end} (UTC)", "Periodo: {start} – {end} (UTC)", "期间：{start} ～ {end}（UTC）", "अवधि: {start} – {end} (UTC)"],
"暦 {name}": ["ephemeris {name}", "éphéméride {name}", "эфемериды {name}", "efeméride {name}", "历表 {name}", "एफ़ेमेरिस {name}"],
"ΔT {v} 秒（手動）": ["ΔT {v} s (manual)", "ΔT {v} s (manuel)", "ΔT {v} с (вручную)", "ΔT {v} s (manual)", "ΔT {v} 秒（手动）", "ΔT {v} से. (मैन्युअल)"],
"ΔT 約 {v} 秒（期間中央）": ["ΔT about {v} s (middle of the period)", "ΔT environ {v} s (milieu de la période)", "ΔT около {v} с (середина периода)", "ΔT aprox. {v} s (mitad del periodo)", "ΔT 约 {v} 秒（期间中点）", "ΔT लगभग {v} से. (अवधि का मध्य)"],
"計算 {s} 秒": ["computed in {s} s", "calculé en {s} s", "расчёт {s} с", "calculado en {s} s", "计算用时 {s} 秒", "गणना {s} से. में"],
"日付": ["Date", "Date", "Дата", "Fecha", "日期", "तिथि"],
"種類": ["Type", "Type", "Тип", "Tipo", "类型", "प्रकार"],
"最大食の時刻": ["Time of greatest eclipse", "Heure du maximum", "Время наибольшей фазы", "Hora del máximo", "食甚时刻", "अधिकतम ग्रहण का समय"],
"食分": ["Magnitude", "Magnitude", "Фаза", "Magnitud", "食分", "परिमाण"],
"中心食の継続": ["Central duration", "Durée centrale", "Длительность центральной фазы", "Duración central", "中心食持续时间", "केंद्रीय अवधि"],
"中心食帯の幅": ["Path width", "Largeur de la bande", "Ширина полосы", "Ancho de la franja", "中心食带宽度", "पथ की चौड़ाई"],
"最大食の地点": ["Point of greatest eclipse", "Lieu du maximum", "Точка наибольшей фазы", "Punto del máximo", "食甚地点", "अधिकतम ग्रहण का स्थान"],
"サロス": ["Saros", "Saros", "Сарос", "Saros", "沙罗周期", "सारोस"],
"最大の時刻": ["Time of maximum", "Heure du maximum", "Время максимума", "Hora del máximo", "最大时刻", "अधिकतम का समय"],
"規模": ["Size", "Ampleur", "Величина", "Magnitud", "规模", "मान"],
"太陽が隠れる割合": ["Sun covered", "Part du Soleil masquée", "Доля закрытого Солнца", "Parte del Sol cubierta", "太阳被遮比例", "सूर्य का ढका भाग"],
"継続時間": ["Duration", "Durée", "Длительность", "Duración", "持续时间", "अवधि"],
"最大時の太陽": ["Sun at maximum", "Soleil au maximum", "Солнце в максимуме", "Sol en el máximo", "最大时的太阳", "अधिकतम पर सूर्य"],
"最大時の衛星位置": ["Satellite at maximum", "Satellite au maximum", "Спутник в максимуме", "Satélite en el máximo", "最大时的卫星位置", "अधिकतम पर उपग्रह"],
"見えるか": ["Visible?", "Visible ?", "Видно?", "¿Visible?", "是否可见", "दिखेगा?"],
"該当する現象はありません。期間や観測者を変えてお試しください。": ["No events found. Try another period or observer.", "Aucun événement. Essayez une autre période ou un autre observateur.", "Явлений не найдено. Попробуйте другой период или наблюдателя.", "No hay eventos. Pruebe otro periodo u observador.", "没有符合条件的天象。请更改期间或观测者后重试。", "कोई घटना नहीं मिली। कोई और अवधि या प्रेक्षक चुनकर देखें।"],
"日食": ["Solar eclipse", "Éclipse de Soleil", "Солнечное затмение", "Eclipse de Sol", "日食", "सूर्य ग्रहण"],
"{what} {n} 件": ["{what}: {n}", "{what} : {n}", "{what}: {n}", "{what}: {n}", "{what} {n} 个", "{what}: {n}"],
"現象なし": ["No events", "Aucun événement", "Явлений нет", "Sin eventos", "无天象", "कोई घटना नहीं"],
"（平均近点角を {step}° ずつ変えた {n} 通りで計算）": [" (computed for {n} mean anomalies, {step}° apart)", " (calculé pour {n} anomalies moyennes espacées de {step}°)", " (расчёт для {n} значений средней аномалии с шагом {step}°)", " (calculado para {n} anomalías medias separadas {step}°)", "（以 {step}° 为步长改变平近点角，共计算 {n} 种）", " ({step}° के अंतराल पर {n} माध्य असंगतियों के लिए गणना)"],
"衛星が軌道上のどこにいるかで結果が変わります。行をクリックすると、平均近点角ごとの結果が表示されます。": ["The results depend on where the satellite is along its orbit. Click a row to see the result for each mean anomaly.", "Les résultats dépendent de la position du satellite sur son orbite. Cliquez sur une ligne pour voir le résultat de chaque anomalie moyenne.", "Результаты зависят от положения спутника на орбите. Щёлкните строку, чтобы увидеть результат для каждой средней аномалии.", "Los resultados dependen de dónde esté el satélite en su órbita. Haga clic en una fila para ver el resultado de cada anomalía media.", "结果取决于卫星在轨道上的位置。点击某一行可查看各平近点角的结果。", "परिणाम इस पर निर्भर हैं कि उपग्रह अपनी कक्षा में कहाँ है। हर माध्य असंगति का परिणाम देखने के लिए किसी पंक्ति पर क्लिक करें।"],
"現象": ["Phenomenon", "Phénomène", "Явление", "Fenómeno", "天象", "घटना"],
"見える位相": ["Phases where visible", "Phases où visible", "Фазы с видимостью", "Fases con visibilidad", "可见的相位", "दृश्य चरण"],
"見える回数": ["Times visible", "Nombre de fois visible", "Сколько раз видно", "Veces visible", "可见次数", "कितनी बार दिखेगा"],
"最も深い食の食分": ["Magnitude of the deepest eclipse", "Magnitude de l’éclipse la plus profonde", "Фаза самого глубокого затмения", "Magnitud del eclipse más profundo", "最深食的食分", "सबसे गहरे ग्रहण का परिमाण"],
"皆既・金環になる位相": ["Phases with total / annular", "Phases totales / annulaires", "Фазы с полным / кольцеобразным", "Fases con total / anular", "成为全食·环食的相位", "पूर्ण / वलयाकार वाले चरण"],
"最大の時刻の範囲": ["Range of times of maximum", "Plage des heures du maximum", "Диапазон времени максимума", "Rango de horas del máximo", "最大时刻的范围", "अधिकतम के समय की सीमा"],
"{n} 回": ["{n}×", "{n} fois", "{n}×", "{n}×", "{n} 次", "{n} बार"],
"{a}〜{b} 回": ["{a}–{b}×", "{a} à {b} fois", "{a}–{b}×", "{a}–{b}×", "{a}～{b} 次", "{a}–{b} बार"],
"なし": ["none", "aucune", "нет", "ninguna", "无", "कोई नहीं"],
"この期間には、どの位相でも見られる現象がありません。": ["No event is visible in this period, whatever the phase.", "Aucun événement n’est visible pendant cette période, quelle que soit la phase.", "В этот период ни при какой фазе явления не видны.", "En este periodo no hay eventos visibles en ninguna fase.", "此期间内无论哪个相位都没有可见的天象。", "इस अवधि में किसी भी चरण पर कोई घटना दिखाई नहीं देती।"],
"{date} の{what}：平均近点角ごとの結果": ["{what}, {date}: results by mean anomaly", "{what} du {date} : résultats par anomalie moyenne", "{what}, {date}: результаты по средней аномалии", "{what} del {date}: resultados por anomalía media", "{date}的{what}：各平近点角的结果", "{date} का {what}: माध्य असंगति के अनुसार परिणाम"],
"各位相で最も長く見える通過を表示しています。行をクリックすると詳細が開きます。": ["For each phase the transit visible for the longest time is shown. Click a row for the details.", "Pour chaque phase, le transit visible le plus longtemps est affiché. Cliquez sur une ligne pour les détails.", "Для каждой фазы показано прохождение, видимое дольше всего. Щёлкните строку, чтобы открыть подробности.", "Para cada fase se muestra el tránsito visible durante más tiempo. Haga clic en una fila para ver los detalles.", "各相位显示可见时间最长的凌日。点击某一行可打开详细信息。", "हर चरण के लिए सबसे लंबे समय तक दिखने वाला पारगमन दिखाया गया है। विवरण के लिए किसी पंक्ति पर क्लिक करें।"],
"各位相で最も深い食を表示しています（1 周回ごとに複数回起きることがあります）。行をクリックすると詳細が開きます。": ["For each phase the deepest eclipse is shown (there can be several, one per orbit). Click a row for the details.", "Pour chaque phase, l’éclipse la plus profonde est affichée (il peut y en avoir plusieurs, une par orbite). Cliquez sur une ligne pour les détails.", "Для каждой фазы показано самое глубокое затмение (их может быть несколько — по одному на виток). Щёлкните строку, чтобы открыть подробности.", "Para cada fase se muestra el eclipse más profundo (puede haber varios, uno por órbita). Haga clic en una fila para ver los detalles.", "各相位显示最深的一次食（每绕一圈可能发生一次，可能有多次）。点击某一行可打开详细信息。", "हर चरण के लिए सबसे गहरा ग्रहण दिखाया गया है (हर परिक्रमा में एक, कई हो सकते हैं)। विवरण के लिए किसी पंक्ति पर क्लिक करें।"],
"平均近点角": ["Mean anomaly", "Anomalie moyenne", "Средняя аномалия", "Anomalía media", "平近点角", "माध्य असंगति"],
"中心間距離": ["Separation of centres", "Distance des centres", "Расстояние между центрами", "Distancia entre centros", "中心间距", "केंद्रों की दूरी"],
"この位相では見られません": ["Not visible at this phase", "Non visible à cette phase", "При этой фазе не видно", "No visible en esta fase", "此相位不可见", "इस चरण पर दिखाई नहीं देगा"],
"平均近点角ごとの見える割合": ["Visible fraction by mean anomaly", "Fraction visible par anomalie moyenne", "Доля видимости по средней аномалии", "Fracción visible por anomalía media", "各平近点角的可见比例", "माध्य असंगति के अनुसार दृश्य भाग"],
"平均近点角ごとの最大食分": ["Greatest magnitude by mean anomaly", "Magnitude maximale par anomalie moyenne", "Наибольшая фаза по средней аномалии", "Magnitud máxima por anomalía media", "各平近点角的最大食分", "माध्य असंगति के अनुसार अधिकतम परिमाण"],
"平均近点角 {m}°：{what}": ["Mean anomaly {m}°: {what}", "Anomalie moyenne {m}° : {what}", "Средняя аномалия {m}°: {what}", "Anomalía media {m}°: {what}", "平近点角 {m}°：{what}", "माध्य असंगति {m}°: {what}"],
"見える割合 {v}": ["visible {v}", "visible à {v}", "видно {v}", "visible {v}", "可见比例 {v}", "दृश्य {v}"],
"食分 {v}": ["magnitude {v}", "magnitude {v}", "фаза {v}", "magnitud {v}", "食分 {v}", "परिमाण {v}"],
"元期での平均近点角（衛星が軌道上のどこにいるか）": ["Mean anomaly at epoch (where the satellite is along its orbit)", "Anomalie moyenne à l’époque (position du satellite sur son orbite)", "Средняя аномалия на эпоху (где спутник находится на орбите)", "Anomalía media en la época (dónde está el satélite en su órbita)", "历元时的平近点角（卫星在轨道上的位置）", "युग पर माध्य असंगति (उपग्रह कक्षा में कहाँ है)"],
"中心間 {v}″": ["separation {v}″", "distance {v}″", "расстояние {v}″", "distancia {v}″", "中心间距 {v}″", "दूरी {v}″"],
"地球中心から見た値": ["as seen from the centre of the Earth", "vu du centre de la Terre", "из центра Земли", "visto desde el centro de la Tierra", "从地心看的值", "पृथ्वी के केंद्र से"],
"（地心）": ["(geocentre)", "(géocentre)", "(геоцентр)", "(geocentro)", "（地心）", "(भूकेंद्र)"],
"◎ 全経過": ["◎ whole event", "◎ en entier", "◎ полностью", "◎ completo", "◎ 全过程", "◎ पूरी घटना"],
"○ 一部 {p}%": ["○ partly {p}%", "○ en partie {p} %", "○ частично {p}%", "○ en parte {p}%", "○ 部分 {p}%", "○ आंशिक {p}%"],
"（日の入り帯食）": [" (in progress at sunset)", " (au coucher du Soleil)", " (на закате)", " (al ponerse el Sol)", "（带食日落）", " (सूर्यास्त के समय)"],
"（日の出帯食）": [" (in progress at sunrise)", " (au lever du Soleil)", " (на восходе)", " (al salir el Sol)", "（带食日出）", " (सूर्योदय के समय)"],
"（地球に隠される）": [" (hidden by the Earth)", " (caché par la Terre)", " (закрыто Землёй)", " (oculto por la Tierra)", "（被地球遮挡）", " (पृथ्वी से छिपा)"],
"× 地平線の下": ["× below the horizon", "× sous l’horizon", "× под горизонтом", "× bajo el horizonte", "× 在地平线下", "× क्षितिज के नीचे"],
"× 地球に隠される": ["× hidden by the Earth", "× caché par la Terre", "× закрыто Землёй", "× oculto por la Tierra", "× 被地球遮挡", "× पृथ्वी से छिपा"],
"皆既 {c}／全体 {d}": ["total {c} / whole {d}", "totalité {c} / totale {d}", "полная фаза {c} / всего {d}", "total {c} / completo {d}", "全食 {c}／全程 {d}", "पूर्ण {c} / कुल {d}"],
"金環 {c}／全体 {d}": ["annular {c} / whole {d}", "annularité {c} / totale {d}", "кольцо {c} / всего {d}", "anular {c} / completo {d}", "环食 {c}／全程 {d}", "वलय {c} / कुल {d}"],
"高度 {v}°": ["altitude {v}°", "hauteur {v}°", "высота {v}°", "altura {v}°", "高度 {v}°", "ऊँचाई {v}°"],
"「{name}」は JSON ファイルとして読めません": ["“{name}” cannot be read as a JSON file", "« {name} » n’est pas un fichier JSON lisible", "Файл «{name}» не читается как JSON", "«{name}» no se puede leer como archivo JSON", "无法将“{name}”作为 JSON 文件读取", "“{name}” को JSON फ़ाइल के रूप में नहीं पढ़ा जा सकता"],
"このファイルは解釈の確認（--dry-run）の出力で、計算結果を含みません": ["This file is the output of a check of the input (--dry-run) and has no results", "Ce fichier est la sortie d’une vérification de la saisie (--dry-run) et ne contient aucun résultat", "Этот файл — вывод проверки входных данных (--dry-run), результатов в нём нет", "Este archivo es la salida de una comprobación de la entrada (--dry-run) y no contiene resultados", "此文件是解析确认（--dry-run）的输出，不含计算结果", "यह फ़ाइल इनपुट की जाँच (--dry-run) का आउटपुट है, इसमें परिणाम नहीं हैं"],
"「{name}」は日食計算機で保存した結果ではありません（「結果を保存」で保存した JSON、詳細の「JSON をダウンロード」、cli.py の --format json の出力を開けます）": ["“{name}” is not a result saved by the eclipse calculator (you can open the JSON of “Save results”, of “Download JSON” in the details, and the output of cli.py --format json)", "« {name} » n’est pas un résultat enregistré par le calculateur (vous pouvez ouvrir le JSON de « Enregistrer les résultats », celui de « Télécharger le JSON » des détails et la sortie de cli.py --format json)", "«{name}» — это не результат, сохранённый калькулятором (можно открыть JSON из «Сохранить результаты», из «Скачать JSON» в подробностях и вывод cli.py --format json)", "«{name}» no es un resultado guardado por la calculadora (puede abrir el JSON de «Guardar resultados», el de «Descargar JSON» de los detalles y la salida de cli.py --format json)", "“{name}”不是日食计算器保存的结果（可以打开“保存结果”保存的 JSON、详细信息中“下载 JSON”的文件以及 cli.py --format json 的输出）", "“{name}” ग्रहण कैलकुलेटर द्वारा सहेजा गया परिणाम नहीं है (आप “परिणाम सहेजें” का JSON, विवरण में “JSON डाउनलोड करें” की फ़ाइल और cli.py --format json का आउटपुट खोल सकते हैं)"],
"「{name}」を開きました": ["Opened “{name}”", "« {name} » ouvert", "Открыт файл «{name}»", "Se abrió «{name}»", "已打开“{name}”", "“{name}” खोली गई"],
"「{name}」を開きました（保存した現象）": ["Opened “{name}” (a saved event)", "« {name} » ouvert (événement enregistré)", "Открыт файл «{name}» (сохранённое событие)", "Se abrió «{name}» (evento guardado)", "已打开“{name}”（保存的天象）", "“{name}” खोली गई (सहेजी गई घटना)"],
"{when} に保存": ["saved {when}", "enregistré le {when}", "сохранено {when}", "guardado el {when}", "保存于 {when}", "{when} को सहेजा गया"],
"詳細は保存した計算条件で再計算します。": ["The details are computed again with the saved settings.", "Les détails sont recalculés avec les conditions enregistrées.", "Подробности пересчитываются по сохранённым условиям.", "Los detalles se recalculan con las condiciones guardadas.", "详细信息将按保存的计算条件重新计算。", "विवरण सहेजी गई शर्तों से फिर से गणना किए जाते हैं।"],
"このファイルには計算条件がないため、詳細は表示できません。": ["This file has no computation settings, so the details cannot be shown.", "Ce fichier ne contient pas les conditions de calcul : les détails ne peuvent pas être affichés.", "В этом файле нет условий расчёта, поэтому подробности недоступны.", "Este archivo no contiene las condiciones de cálculo, así que no se pueden mostrar los detalles.", "此文件中没有计算条件，无法显示详细信息。", "इस फ़ाइल में गणना की शर्तें नहीं हैं, इसलिए विवरण नहीं दिखाए जा सकते।"],
"保存した結果を表示しています（{name}{when}）。": ["Showing saved results ({name}{when}).", "Résultats enregistrés affichés ({name}{when}).", "Показаны сохранённые результаты ({name}{when}).", "Se muestran resultados guardados ({name}{when}).", "正在显示保存的结果（{name}{when}）。", "सहेजे गए परिणाम दिखाए जा रहे हैं ({name}{when})।"],
"このファイルには計算条件（request）がないため、詳細を計算できません": ["This file has no computation settings (request), so the details cannot be computed", "Ce fichier ne contient pas les conditions de calcul (request) : impossible de calculer les détails", "В этом файле нет условий расчёта (request), поэтому подробности рассчитать нельзя", "Este archivo no contiene las condiciones de cálculo (request), así que no se pueden calcular los detalles", "此文件中没有计算条件（request），无法计算详细信息", "इस फ़ाइल में गणना की शर्तें (request) नहीं हैं, इसलिए विवरण की गणना नहीं हो सकती"],
"保存した計算条件で詳細を計算中…": ["Computing the details with the saved settings…", "Calcul des détails avec les conditions enregistrées…", "Расчёт подробностей по сохранённым условиям…", "Calculando los detalles con las condiciones guardadas…", "正在按保存的计算条件计算详细信息…", "सहेजी गई शर्तों से विवरण की गणना हो रही है…"],
"詳細を計算中…": ["Computing the details…", "Calcul des détails…", "Расчёт подробностей…", "Calculando los detalles…", "正在计算详细信息…", "विवरण की गणना हो रही है…"],
"{date} の{type}": ["{type}, {date}", "{type} du {date}", "{type}, {date}", "{type} del {date}", "{date}的{type}", "{date} का {type}"],
"地球全体での状況": ["Circumstances for the whole Earth", "Circonstances pour la Terre entière", "Обстоятельства для всей Земли", "Circunstancias para toda la Tierra", "全球范围的情况", "पूरी पृथ्वी के लिए स्थिति"],
"<b>{date}</b> の{type}です。": ["{type} of <b>{date}</b>.", "{type} du <b>{date}</b>.", "{type}: <b>{date}</b>", "{type} del <b>{date}</b>.", "<b>{date}</b>的{type}。", "<b>{date}</b> का {type}।"],
"食が最も大きくなる「最大食」は <b>{time}</b> に <b>{place}</b> で起こり、太陽高度は {alt}° です。": ["Greatest eclipse occurs at <b>{time}</b> at <b>{place}</b>, with the Sun {alt}° high.", "Le maximum de l’éclipse a lieu à <b>{time}</b> en <b>{place}</b>, le Soleil étant à {alt}° de hauteur.", "Наибольшая фаза наступает в <b>{time}</b> в точке <b>{place}</b>, высота Солнца {alt}°.", "El máximo del eclipse ocurre a las <b>{time}</b> en <b>{place}</b>, con el Sol a {alt}° de altura.", "食甚发生在 <b>{time}</b>，地点 <b>{place}</b>，太阳高度 {alt}°。", "अधिकतम ग्रहण <b>{time}</b> पर <b>{place}</b> पर होता है, तब सूर्य {alt}° ऊँचा होता है।"],
"中心食の継続時間は最大 <b>{dur}</b> です。": ["The central eclipse lasts up to <b>{dur}</b>.", "La phase centrale dure jusqu’à <b>{dur}</b>.", "Центральная фаза длится до <b>{dur}</b>.", "La fase central dura hasta <b>{dur}</b>.", "中心食最长持续 <b>{dur}</b>。", "केंद्रीय ग्रहण अधिकतम <b>{dur}</b> तक रहता है।"],
"中心食の継続時間は最大 <b>{dur}</b>、金環帯の幅は約 <b>{width} km</b> です。": ["Annularity lasts up to <b>{dur}</b> and the path of annularity is about <b>{width} km</b> wide.", "L’annularité dure jusqu’à <b>{dur}</b> et la bande d’annularité mesure environ <b>{width} km</b> de large.", "Кольцеобразная фаза длится до <b>{dur}</b>, ширина полосы около <b>{width} км</b>.", "La anularidad dura hasta <b>{dur}</b> y la franja de anularidad mide unos <b>{width} km</b> de ancho.", "中心食最长持续 <b>{dur}</b>，环食带宽约 <b>{width} km</b>。", "वलय अधिकतम <b>{dur}</b> तक रहता है और वलय पथ लगभग <b>{width} किमी</b> चौड़ा है।"],
"中心食の継続時間は最大 <b>{dur}</b>、皆既帯の幅は約 <b>{width} km</b> です。": ["Totality lasts up to <b>{dur}</b> and the path of totality is about <b>{width} km</b> wide.", "La totalité dure jusqu’à <b>{dur}</b> et la bande de totalité mesure environ <b>{width} km</b> de large.", "Полная фаза длится до <b>{dur}</b>, ширина полосы около <b>{width} км</b>.", "La totalidad dura hasta <b>{dur}</b> y la franja de totalidad mide unos <b>{width} km</b> de ancho.", "中心食最长持续 <b>{dur}</b>，全食带宽约 <b>{width} km</b>。", "पूर्णता अधिकतम <b>{dur}</b> तक रहती है और पूर्णता पथ लगभग <b>{width} किमी</b> चौड़ा है।"],
"最大食分は {mag} の部分日食で、皆既・金環になる場所はありません。": ["It is a partial eclipse of greatest magnitude {mag}; it is total or annular nowhere.", "C’est une éclipse partielle de magnitude maximale {mag} ; elle n’est totale ni annulaire nulle part.", "Это частное затмение с наибольшей фазой {mag}; полным или кольцеобразным оно не бывает нигде.", "Es un eclipse parcial de magnitud máxima {mag}; no es total ni anular en ningún lugar.", "这是最大食分 {mag} 的日偏食，任何地方都不会成为全食或环食。", "यह अधिकतम परिमाण {mag} का आंशिक ग्रहण है; कहीं भी पूर्ण या वलयाकार नहीं होगा।"],
"地球上のどこかで部分食が見られるのは {start} 〜 {end}（{tz}）です。": ["The partial eclipse can be seen somewhere on Earth from {start} to {end} ({tz}).", "L’éclipse partielle est visible quelque part sur Terre de {start} à {end} ({tz}).", "Частное затмение видно где-либо на Земле с {start} до {end} ({tz}).", "El eclipse parcial se ve en algún lugar de la Tierra de {start} a {end} ({tz}).", "地球上某处可见偏食的时间为 {start} ～ {end}（{tz}）。", "आंशिक ग्रहण पृथ्वी पर कहीं न कहीं {start} से {end} ({tz}) तक दिखाई देता है।"],
"{name}では": ["In {name}", "À {name}", "В пункте «{name}»", "En {name}", "在{name}", "{name} में"],
"{name}から見ると": ["Seen from {name}", "Vu depuis {name}", "С борта «{name}»", "Visto desde {name}", "从{name}看", "{name} से देखने पर"],
"地球中心から見ると": ["Seen from the centre of the Earth", "Vu du centre de la Terre", "Из центра Земли", "Visto desde el centro de la Tierra", "从地心看", "पृथ्वी के केंद्र से देखने पर"],
"{who}、<b>{date}</b> の <b>{c1}</b> に太陽が欠け始め、<b>{max}</b> に最大（食分 <b>{mag}</b>、太陽の面積の <b>{obs}</b> が隠れる）となり、<b>{c4}</b> に終わります（{tz}）。": ["{who}, on <b>{date}</b> the eclipse begins at <b>{c1}</b>, is greatest at <b>{max}</b> (magnitude <b>{mag}</b>, <b>{obs}</b> of the Sun’s area covered) and ends at <b>{c4}</b> ({tz}).", "{who}, le <b>{date}</b>, l’éclipse commence à <b>{c1}</b>, atteint son maximum à <b>{max}</b> (magnitude <b>{mag}</b>, <b>{obs}</b> de la surface du Soleil masquée) et se termine à <b>{c4}</b> ({tz}).", "{who} <b>{date}</b> затмение начинается в <b>{c1}</b>, достигает максимума в <b>{max}</b> (фаза <b>{mag}</b>, закрыто <b>{obs}</b> площади Солнца) и заканчивается в <b>{c4}</b> ({tz}).", "{who}, el <b>{date}</b> el eclipse comienza a las <b>{c1}</b>, alcanza su máximo a las <b>{max}</b> (magnitud <b>{mag}</b>, se cubre el <b>{obs}</b> del área del Sol) y termina a las <b>{c4}</b> ({tz}).", "{who}，<b>{date}</b> <b>{c1}</b> 初亏，<b>{max}</b> 食甚（食分 <b>{mag}</b>，太阳面积的 <b>{obs}</b> 被遮挡），<b>{c4}</b> 复圆（{tz}）。", "{who}, <b>{date}</b> को ग्रहण <b>{c1}</b> पर आरंभ होता है, <b>{max}</b> पर अधिकतम होता है (परिमाण <b>{mag}</b>, सूर्य के क्षेत्रफल का <b>{obs}</b> ढक जाता है) और <b>{c4}</b> पर समाप्त होता है ({tz})।"],
"{start} から {end} までの <b>{dur}</b> 間は<b>皆既日食</b>（太陽が完全に隠れる）です。": ["From {start} to {end}, for <b>{dur}</b>, the eclipse is <b>total</b> (the Sun is completely covered).", "De {start} à {end}, pendant <b>{dur}</b>, l’éclipse est <b>totale</b> (le Soleil est entièrement caché).", "С {start} до {end}, в течение <b>{dur}</b>, затмение <b>полное</b> (Солнце закрыто целиком).", "De {start} a {end}, durante <b>{dur}</b>, el eclipse es <b>total</b> (el Sol queda totalmente cubierto).", "{start} 至 {end} 的 <b>{dur}</b> 内为<b>日全食</b>（太阳被完全遮住）。", "{start} से {end} तक, <b>{dur}</b> के लिए ग्रहण <b>पूर्ण</b> होता है (सूर्य पूरी तरह ढक जाता है)।"],
"{start} から {end} までの <b>{dur}</b> 間は<b>金環日食</b>（太陽がリング状に見える）です。": ["From {start} to {end}, for <b>{dur}</b>, the eclipse is <b>annular</b> (the Sun looks like a ring).", "De {start} à {end}, pendant <b>{dur}</b>, l’éclipse est <b>annulaire</b> (le Soleil forme un anneau).", "С {start} до {end}, в течение <b>{dur}</b>, затмение <b>кольцеобразное</b> (Солнце выглядит как кольцо).", "De {start} a {end}, durante <b>{dur}</b>, el eclipse es <b>anular</b> (el Sol parece un anillo).", "{start} 至 {end} 的 <b>{dur}</b> 内为<b>日环食</b>（太阳呈环状）。", "{start} से {end} तक, <b>{dur}</b> के लिए ग्रहण <b>वलयाकार</b> होता है (सूर्य एक छल्ले जैसा दिखता है)।"],
"（衛星の運動により {n} 回に分かれます）": ["(split into {n} parts by the motion of the satellite)", "(divisée en {n} parties par le mouvement du satellite)", "(из-за движения спутника она разбита на {n} части)", "(dividida en {n} partes por el movimiento del satélite)", "（由于卫星的运动分为 {n} 段）", "(उपग्रह की गति के कारण {n} भागों में बँटा)"],
"{who}、<b>{date}</b> の <b>{c1}</b> に{body}が太陽の縁にかかり始め、<b>{max}</b> に太陽の中心に最も近づき（中心間 {sep}″）、<b>{c4}</b> に太陽面から離れます（{tz}）。": ["{who}, on <b>{date}</b> {body} reaches the limb of the Sun at <b>{c1}</b>, comes closest to the centre of the Sun at <b>{max}</b> (separation {sep}″) and leaves the disc at <b>{c4}</b> ({tz}).", "{who}, le <b>{date}</b>, {body} atteint le bord du Soleil à <b>{c1}</b>, passe au plus près de son centre à <b>{max}</b> (distance {sep}″) et quitte le disque à <b>{c4}</b> ({tz}).", "{who} <b>{date}</b> планета ({body}) касается края Солнца в <b>{c1}</b>, ближе всего подходит к центру Солнца в <b>{max}</b> (расстояние {sep}″) и сходит с диска в <b>{c4}</b> ({tz}).", "{who}, el <b>{date}</b> {body} llega al borde del Sol a las <b>{c1}</b>, se acerca más a su centro a las <b>{max}</b> (distancia {sep}″) y sale del disco a las <b>{c4}</b> ({tz}).", "{who}，<b>{date}</b> <b>{c1}</b> {body}开始接触日面边缘，<b>{max}</b> 最接近日面中心（中心间距 {sep}″），<b>{c4}</b> 离开日面（{tz}）。", "{who}, <b>{date}</b> को {body} <b>{c1}</b> पर सूर्य के किनारे पर पहुँचता है, <b>{max}</b> पर सूर्य के केंद्र के सबसे निकट होता है (दूरी {sep}″) और <b>{c4}</b> पर बिंब से बाहर निकलता है ({tz})।"],
"経過時間は {dur} です。": ["The transit lasts {dur}.", "Le transit dure {dur}.", "Прохождение длится {dur}.", "El tránsito dura {dur}.", "凌日持续 {dur}。", "पारगमन {dur} चलता है।"],
"{body}が太陽の縁をかすめるだけで、全体が太陽面に入ることはありません。": ["{body} only grazes the limb of the Sun and never enters the disc entirely.", "{body} ne fait qu’effleurer le bord du Soleil sans jamais entrer entièrement sur le disque.", "Планета ({body}) лишь касается края Солнца и целиком на диск не входит.", "{body} solo roza el borde del Sol y nunca entra por completo en el disco.", "{body}只擦过日面边缘，不会完全进入日面。", "{body} केवल सूर्य के किनारे को छूता है, पूरी तरह बिंब पर कभी नहीं आता।"],
"全経過で太陽は地平線の上にあり、最大時の太陽高度は <b>{alt}°</b>（{az}の空）です。": ["The Sun is above the horizon throughout; at maximum it is <b>{alt}°</b> high (towards {az}).", "Le Soleil reste au-dessus de l’horizon pendant tout l’événement ; au maximum il est à <b>{alt}°</b> de hauteur (direction {az}).", "Солнце всё время над горизонтом; в максимуме его высота <b>{alt}°</b> (направление {az}).", "El Sol está sobre el horizonte todo el tiempo; en el máximo está a <b>{alt}°</b> de altura (hacia el {az}).", "全过程中太阳都在地平线以上，最大时太阳高度 <b>{alt}°</b>（{az}方天空）。", "पूरी घटना के दौरान सूर्य क्षितिज के ऊपर रहता है; अधिकतम पर उसकी ऊँचाई <b>{alt}°</b> है ({az} दिशा में)।"],
"全経過で太陽は地球に隠されません。": ["The Sun is not hidden by the Earth at any time.", "Le Soleil n’est jamais caché par la Terre.", "Солнце ни разу не закрывается Землёй.", "El Sol no queda oculto por la Tierra en ningún momento.", "全过程中太阳都不会被地球遮挡。", "पूरी घटना के दौरान सूर्य पृथ्वी से नहीं छिपता।"],
"ただし見られるのは <b>{iv}</b> の間だけです（太陽が地平線の下にある時間を除く）。": ["It can be seen only during <b>{iv}</b> (not while the Sun is below the horizon).", "Il n’est visible que pendant <b>{iv}</b> (pas lorsque le Soleil est sous l’horizon).", "Однако видно только в интервале <b>{iv}</b> (когда Солнце над горизонтом).", "Solo se ve durante <b>{iv}</b> (no mientras el Sol está bajo el horizonte).", "但只能在 <b>{iv}</b> 期间看到（太阳在地平线以下的时间除外）。", "लेकिन यह केवल <b>{iv}</b> के दौरान दिखेगा (जब सूर्य क्षितिज के नीचे हो, तब नहीं)।"],
"ただし見られるのは <b>{iv}</b> の間だけです（衛星から見て太陽が地球に隠される時間を除く）。": ["It can be seen only during <b>{iv}</b> (not while the Earth hides the Sun from the satellite).", "Il n’est visible que pendant <b>{iv}</b> (pas lorsque la Terre cache le Soleil au satellite).", "Однако видно только в интервале <b>{iv}</b> (когда Земля не закрывает Солнце от спутника).", "Solo se ve durante <b>{iv}</b> (no mientras la Tierra oculta el Sol al satélite).", "但只能在 <b>{iv}</b> 期间看到（从卫星看太阳被地球遮挡的时间除外）。", "लेकिन यह केवल <b>{iv}</b> के दौरान दिखेगा (जब पृथ्वी उपग्रह से सूर्य को छिपाती है, तब नहीं)।"],
"見える範囲での最大食分は {mag} です。": ["The greatest magnitude while visible is {mag}.", "La magnitude maximale pendant la visibilité est {mag}.", "Наибольшая фаза в видимый период — {mag}.", "La magnitud máxima mientras es visible es {mag}.", "可见期间的最大食分为 {mag}。", "दिखाई देने के दौरान अधिकतम परिमाण {mag} है।"],
"<b>この現象は太陽が地平線の下にあるため見られません。</b>": ["<b>It cannot be seen: the Sun is below the horizon.</b>", "<b>Invisible : le Soleil est sous l’horizon.</b>", "<b>Явление не видно: Солнце под горизонтом.</b>", "<b>No se puede ver: el Sol está bajo el horizonte.</b>", "<b>太阳在地平线以下，看不到此天象。</b>", "<b>यह दिखाई नहीं देगा: सूर्य क्षितिज के नीचे है।</b>"],
"<b>この間、太陽は地球に隠されていて見えません。</b>": ["<b>It cannot be seen: the Earth hides the Sun all the time.</b>", "<b>Invisible : la Terre cache le Soleil pendant tout ce temps.</b>", "<b>Явление не видно: всё это время Солнце закрыто Землёй.</b>", "<b>No se puede ver: la Tierra oculta el Sol todo el tiempo.</b>", "<b>这段时间内太阳被地球遮挡，看不到。</b>", "<b>यह दिखाई नहीं देगा: इस पूरे समय पृथ्वी सूर्य को छिपाए रहती है।</b>"],
"最大時の衛星は {pos} の上空 {alt} km にいます。": ["At maximum the satellite is {alt} km above {pos}.", "Au maximum, le satellite est à {alt} km au-dessus de {pos}.", "В максимуме спутник находится на высоте {alt} км над точкой {pos}.", "En el máximo el satélite está a {alt} km sobre {pos}.", "最大时卫星位于 {pos} 上空 {alt} km。", "अधिकतम पर उपग्रह {pos} के ऊपर {alt} किमी पर है।"],
"これは地球中心から見た標準値で、地上の各地点では視差により数分ずれます。地図タブで地点をクリックすると、その場所での時刻を計算できます。": ["These are standard values for the centre of the Earth; because of parallax, times at a place on the ground differ by a few minutes. Click a place in the Map tab to compute its times.", "Ce sont des valeurs de référence pour le centre de la Terre ; à cause de la parallaxe, les heures diffèrent de quelques minutes en un lieu donné. Cliquez sur un lieu dans l’onglet Carte pour calculer ses heures.", "Это стандартные значения для центра Земли; из-за параллакса в каждом месте на поверхности моменты отличаются на несколько минут. Щёлкните место на вкладке «Карта», чтобы рассчитать время для него.", "Son valores estándar para el centro de la Tierra; por el paralaje, las horas en un lugar de la superficie difieren unos minutos. Haga clic en un lugar en la pestaña Mapa para calcular sus horas.", "这是从地心看的标准值，地面各地因视差会相差几分钟。在“地图”标签页点击地点即可计算该处的时刻。", "ये पृथ्वी के केंद्र के लिए मानक मान हैं; लंबन के कारण धरातल के हर स्थान पर समय कुछ मिनट अलग होता है। किसी स्थान का समय जानने के लिए मानचित्र टैब में उस पर क्लिक करें।"],
"最大食分": ["Greatest magnitude", "Magnitude maximale", "Наибольшая фаза", "Magnitud máxima", "最大食分", "अधिकतम परिमाण"],
"太陽の直径が隠れる割合": ["fraction of the Sun’s diameter covered", "fraction du diamètre solaire masquée", "доля закрытого диаметра Солнца", "fracción del diámetro solar cubierta", "太阳直径被遮挡的比例", "सूर्य के व्यास का ढका भाग"],
"食分（視直径比）": ["Magnitude (diameter ratio)", "Magnitude (rapport des diamètres)", "Фаза (отношение диаметров)", "Magnitud (razón de diámetros)", "食分（视直径比）", "परिमाण (व्यास अनुपात)"],
"月と太陽の見かけの大きさの比": ["ratio of the apparent sizes of the Moon and the Sun", "rapport des tailles apparentes de la Lune et du Soleil", "отношение видимых размеров Луны и Солнца", "razón de los tamaños aparentes de la Luna y el Sol", "月球与太阳视大小之比", "चंद्रमा और सूर्य के आभासी आकार का अनुपात"],
"γ（ガンマ）": ["γ (gamma)", "γ (gamma)", "γ (гамма)", "γ (gamma)", "γ（伽马）", "γ (गामा)"],
"影の軸と地球中心の最接近距離": ["least distance of the shadow axis from the centre of the Earth", "distance minimale de l’axe de l’ombre au centre de la Terre", "наименьшее расстояние оси тени от центра Земли", "distancia mínima del eje de la sombra al centro de la Tierra", "影轴与地心的最近距离", "छाया अक्ष की पृथ्वी के केंद्र से न्यूनतम दूरी"],
"中心食の継続時間": ["Duration of the central eclipse", "Durée de la phase centrale", "Длительность центральной фазы", "Duración de la fase central", "中心食持续时间", "केंद्रीय ग्रहण की अवधि"],
"最大食の地点で": ["at the point of greatest eclipse", "au lieu du maximum", "в точке наибольшей фазы", "en el punto del máximo", "在食甚地点", "अधिकतम ग्रहण के स्थान पर"],
"サロス番号": ["Saros number", "Numéro de saros", "Номер сароса", "Número de saros", "沙罗序列号", "सारोस संख्या"],
"約18年周期の系列": ["series repeating about every 18 years", "série d’environ 18 ans", "серия с периодом около 18 лет", "serie de unos 18 años", "约 18 年周期的序列", "लगभग 18 वर्ष की आवृत्ति वाली श्रृंखला"],
"地球全体での経過": ["Course over the whole Earth", "Déroulement sur la Terre entière", "Ход затмения для всей Земли", "Desarrollo en toda la Tierra", "全球范围的过程", "पूरी पृथ्वी पर क्रम"],
"部分食の始まり（P1）": ["Partial eclipse begins (P1)", "Début de l’éclipse partielle (P1)", "Начало частного затмения (P1)", "Inicio del eclipse parcial (P1)", "偏食始（P1）", "आंशिक ग्रहण आरंभ (P1)"],
"最大食": ["Greatest eclipse", "Maximum de l’éclipse", "Наибольшая фаза", "Máximo del eclipse", "食甚", "अधिकतम ग्रहण"],
"部分食の終わり（P4）": ["Partial eclipse ends (P4)", "Fin de l’éclipse partielle (P4)", "Конец частного затмения (P4)", "Fin del eclipse parcial (P4)", "偏食终（P4）", "आंशिक ग्रहण समाप्त (P4)"],
"段階": ["Stage", "Étape", "Этап", "Etapa", "阶段", "चरण"],
"日時（{tz}）": ["Date and time ({tz})", "Date et heure ({tz})", "Дата и время ({tz})", "Fecha y hora ({tz})", "日期时间（{tz}）", "तिथि और समय ({tz})"],
"太陽高度 {v}°": ["Sun altitude {v}°", "hauteur du Soleil {v}°", "высота Солнца {v}°", "altura del Sol {v}°", "太阳高度 {v}°", "सूर्य की ऊँचाई {v}°"],
"{v} 秒": ["{v} s", "{v} s", "{v} с", "{v} s", "{v} 秒", "{v} से."],
"その他": ["Other", "Autres", "Прочее", "Otros", "其他", "अन्य"],
"最大食の地点での見え方": ["Circumstances at the point of greatest eclipse", "Circonstances au lieu du maximum", "Обстоятельства в точке наибольшей фазы", "Circunstancias en el punto del máximo", "食甚地点的情况", "अधिकतम ग्रहण के स्थान पर स्थिति"],
"「地図」タブで地図上の好きな地点をクリックすると、その地点での見え方（時刻・食分）を計算できます。": ["Click any place in the Map tab to compute the circumstances there (times, magnitude).", "Cliquez sur un lieu dans l’onglet Carte pour calculer les circonstances en ce lieu (heures, magnitude).", "Щёлкните любое место на вкладке «Карта», чтобы рассчитать обстоятельства для него (моменты, фаза).", "Haga clic en cualquier lugar en la pestaña Mapa para calcular las circunstancias allí (horas, magnitud).", "在“地图”标签页点击任意地点，即可计算该地点的情况（时刻、食分）。", "मानचित्र टैब में किसी भी स्थान पर क्लिक करके वहाँ की स्थिति (समय, परिमाण) की गणना करें।"],
"食面積率": ["Obscuration", "Obscuration", "Доля закрытой площади", "Oscurecimiento", "食面积比", "आच्छादन"],
"太陽の面積が隠れる割合": ["fraction of the Sun’s area covered", "fraction de la surface solaire masquée", "доля закрытой площади Солнца", "fracción del área solar cubierta", "太阳面积被遮挡的比例", "सूर्य के क्षेत्रफल का ढका भाग"],
"皆既の継続時間": ["Duration of totality", "Durée de la totalité", "Длительность полной фазы", "Duración de la totalidad", "全食持续时间", "पूर्णता की अवधि"],
"金環の継続時間": ["Duration of annularity", "Durée de l’annularité", "Длительность кольцеобразной фазы", "Duración de la anularidad", "环食持续时间", "वलय की अवधि"],
"最長区間（{n}回）": ["longest part (of {n})", "partie la plus longue (sur {n})", "самая длинная часть (из {n})", "parte más larga (de {n})", "最长一段（共 {n} 段）", "सबसे लंबा भाग ({n} में से)"],
"第2〜第3接触": ["2nd to 3rd contact", "du 2e au 3e contact", "со 2-го по 3-й контакт", "del 2.º al 3.er contacto", "第二至第三接触", "द्वितीय से तृतीय स्पर्श"],
"食の継続時間": ["Duration of the eclipse", "Durée de l’éclipse", "Длительность затмения", "Duración del eclipse", "食的持续时间", "ग्रहण की अवधि"],
"第1〜第4接触": ["1st to 4th contact", "du 1er au 4e contact", "с 1-го по 4-й контакт", "del 1.er al 4.º contacto", "第一至第四接触", "प्रथम से चतुर्थ स्पर्श"],
"視直径比（月/太陽）": ["Diameter ratio (Moon/Sun)", "Rapport des diamètres (Lune/Soleil)", "Отношение диаметров (Луна/Солнце)", "Razón de diámetros (Luna/Sol)", "视直径比（月/日）", "व्यास अनुपात (चंद्रमा/सूर्य)"],
"月の方が大きい": ["the Moon is larger", "la Lune est plus grande", "Луна больше", "la Luna es mayor", "月球较大", "चंद्रमा बड़ा है"],
"月の方が小さい": ["the Moon is smaller", "la Lune est plus petite", "Луна меньше", "la Luna es menor", "月球较小", "चंद्रमा छोटा है"],
"太陽中心との最小距離": ["Least distance from the centre of the Sun", "Distance minimale au centre du Soleil", "Наименьшее расстояние от центра Солнца", "Distancia mínima al centro del Sol", "与日面中心的最小距离", "सूर्य के केंद्र से न्यूनतम दूरी"],
"太陽の半径は {v}″": ["the Sun’s radius is {v}″", "le rayon du Soleil est de {v}″", "радиус Солнца {v}″", "el radio del Sol es {v}″", "太阳半径为 {v}″", "सूर्य की त्रिज्या {v}″ है"],
"{body}の視直径": ["Apparent diameter of {body}", "Diamètre apparent de {body}", "Видимый диаметр ({body})", "Diámetro aparente de {body}", "{body}的视直径", "{body} का आभासी व्यास"],
"太陽の約 1/{n}": ["about 1/{n} of the Sun", "environ 1/{n} du Soleil", "около 1/{n} Солнца", "aprox. 1/{n} del Sol", "约为太阳的 1/{n}", "सूर्य का लगभग 1/{n}"],
"経過時間": ["Duration", "Durée", "Длительность", "Duración", "经过时间", "अवधि"],
"内接している時間": ["Time inside the disc", "Durée à l’intérieur du disque", "Время внутри диска", "Tiempo dentro del disco", "内切期间", "बिंब के भीतर का समय"],
"最大時の太陽高度": ["Sun altitude at maximum", "Hauteur du Soleil au maximum", "Высота Солнца в максимуме", "Altura del Sol en el máximo", "最大时的太阳高度", "अधिकतम पर सूर्य की ऊँचाई"],
"方位 {v}°": ["azimuth {v}°", "azimut {v}°", "азимут {v}°", "acimut {v}°", "方位角 {v}°", "दिगंश {v}°"],
"見える時間の割合": ["Fraction of time visible", "Fraction du temps visible", "Доля времени видимости", "Fracción del tiempo visible", "可见时间比例", "दृश्य समय का भाग"],
"太陽が地球に隠されない時間": ["time the Sun is not hidden by the Earth", "temps où la Terre ne cache pas le Soleil", "время, когда Солнце не закрыто Землёй", "tiempo en que la Tierra no oculta el Sol", "太阳未被地球遮挡的时间", "समय जब सूर्य पृथ्वी से नहीं छिपता"],
"接触時刻と状況": ["Contacts and circumstances", "Contacts et circonstances", "Контакты и обстоятельства", "Contactos y circunstancias", "接触时刻与情况", "स्पर्श और स्थिति"],
"暦": ["Ephemeris", "Éphéméride", "Эфемериды", "Efeméride", "历表", "एफ़ेमेरिस"],
"ΔT（TT−UT）": ["ΔT (TT−UT)", "ΔT (TT−UT)", "ΔT (TT−UT)", "ΔT (TT−UT)", "ΔT（TT−UT）", "ΔT (TT−UT)"],
"太陽の視直径（最大時）": ["Apparent diameter of the Sun (at maximum)", "Diamètre apparent du Soleil (au maximum)", "Видимый диаметр Солнца (в максимуме)", "Diámetro aparente del Sol (en el máximo)", "太阳视直径（最大时）", "सूर्य का आभासी व्यास (अधिकतम पर)"],
"{body}の視直径（最大時）": ["Apparent diameter of {body} (at maximum)", "Diamètre apparent de {body} (au maximum)", "Видимый диаметр ({body}) в максимуме", "Diámetro aparente de {body} (en el máximo)", "{body}视直径（最大时）", "{body} का आभासी व्यास (अधिकतम पर)"],
"計算モデル": ["Model", "Modèle", "Модель", "Modelo", "计算模型", "मॉडल"],
"太陽半径 {v} km": ["Sun radius {v} km", "rayon du Soleil {v} km", "радиус Солнца {v} км", "radio del Sol {v} km", "太阳半径 {v} km", "सूर्य की त्रिज्या {v} किमी"],
"月半径 {ext} / {int} km（外接/内接）": ["Moon radius {ext} / {int} km (external/internal contacts)", "rayon de la Lune {ext} / {int} km (contacts extérieurs/intérieurs)", "радиус Луны {ext} / {int} км (внешние/внутренние контакты)", "radio de la Luna {ext} / {int} km (contactos externos/internos)", "月球半径 {ext} / {int} km（外切/内切）", "चंद्रमा की त्रिज्या {ext} / {int} किमी (बाह्य/आंतरिक स्पर्श)"],
"{body}半径 {v} km": ["radius of {body} {v} km", "rayon de {body} {v} km", "радиус ({body}) {v} км", "radio de {body} {v} km", "{body}半径 {v} km", "{body} की त्रिज्या {v} किमी"],
"計算条件": ["Computation settings", "Conditions de calcul", "Условия расчёта", "Condiciones de cálculo", "计算条件", "गणना की शर्तें"],
"※ 計算期間の端にかかっているため、一部の接触時刻は期間の端の値です。": ["* The event extends beyond the computed period; some contact times are the ends of the period.", "* L’événement dépasse la période calculée ; certaines heures de contact sont les bornes de la période.", "* Событие выходит за пределы периода расчёта; некоторые моменты контактов — это границы периода.", "* El evento se sale del periodo calculado; algunas horas de contacto son los extremos del periodo.", "※ 由于跨越了计算期间的端点，部分接触时刻为期间端点的值。", "* घटना गणना की अवधि से बाहर तक जाती है; कुछ स्पर्श समय अवधि के छोर हैं।"],
"接触": ["Contact", "Contact", "Контакт", "Contacto", "接触", "स्पर्श"],
"時刻（{tz}）": ["Time ({tz})", "Heure ({tz})", "Время ({tz})", "Hora ({tz})", "时刻（{tz}）", "समय ({tz})"],
"太陽高度": ["Sun altitude", "Hauteur du Soleil", "Высота Солнца", "Altura del Sol", "太阳高度", "सूर्य की ऊँचाई"],
"方位": ["Azimuth", "Azimut", "Азимут", "Acimut", "方位角", "दिगंश"],
"衛星直下点": ["Sub-satellite point", "Point subsatellite", "Подспутниковая точка", "Punto subsatelital", "星下点", "उपग्रह-अधो बिंदु"],
"高度": ["Altitude", "Altitude", "Высота", "Altitud", "高度", "ऊँचाई"],
"地球の縁からの太陽の離角": ["Sun’s distance from the Earth’s limb", "Écart du Soleil au bord de la Terre", "Угол Солнца над краем Земли", "Separación del Sol del borde terrestre", "太阳与地球边缘的角距", "पृथ्वी के किनारे से सूर्य की कोणीय दूरी"],
"位置角 P": ["Position angle P", "Angle de position P", "Позиционный угол P", "Ángulo de posición P", "位置角 P", "स्थिति कोण P"],
"天頂角 V": ["Zenith angle V", "Angle zénithal V", "Зенитный угол V", "Ángulo cenital V", "天顶角 V", "शिरोबिंदु कोण V"],
"観測": ["Observation", "Observation", "Наблюдение", "Observación", "观测", "प्रेक्षण"],
"見える": ["visible", "visible", "видно", "visible", "可见", "दिखेगा"],
"地平線下": ["below horizon", "sous l’horizon", "под горизонтом", "bajo el horizonte", "地平线下", "क्षितिज के नीचे"],
"地球に隠れる": ["hidden by Earth", "caché par la Terre", "закрыто Землёй", "oculto por la Tierra", "被地球遮挡", "पृथ्वी से छिपा"],
"天頂が上（見たままの向き）": ["Zenith up (as seen in the sky)", "Zénith en haut (comme dans le ciel)", "Зенит вверху (как на небе)", "Cenit arriba (como en el cielo)", "天顶朝上（所见方向）", "शिरोबिंदु ऊपर (जैसा आकाश में दिखे)"],
"天の北が上（天文図の向き）": ["Celestial north up (as in star charts)", "Nord céleste en haut (comme sur les cartes)", "Север мира вверху (как на картах)", "Norte celeste arriba (como en las cartas)", "天北朝上（星图方向）", "खगोलीय उत्तर ऊपर (तारा मानचित्र जैसा)"],
"地球の方向が下": ["Earth down", "Terre en bas", "Земля внизу", "Tierra abajo", "地球方向朝下", "पृथ्वी नीचे"],
"空を見上げたときの見え方です（大きさは太陽が基準）。地平線は緑で表示します。": ["As seen looking up at the sky (scaled to the Sun). The horizon is shown in green.", "Vue en regardant le ciel (à l’échelle du Soleil). L’horizon est en vert.", "Вид при взгляде на небо (масштаб по Солнцу). Горизонт показан зелёным.", "Vista mirando al cielo (a escala del Sol). El horizonte se muestra en verde.", "仰望天空时的样子（大小以太阳为基准）。地平线以绿色表示。", "आकाश की ओर देखने पर दृश्य (सूर्य के अनुपात में)। क्षितिज हरे रंग में है।"],
"衛星から太陽方向を見た様子です。紺色の大きな円弧は地球の縁です。": ["Looking towards the Sun from the satellite. The large dark blue arc is the limb of the Earth.", "Vue vers le Soleil depuis le satellite. Le grand arc bleu foncé est le bord de la Terre.", "Вид на Солнце со спутника. Большая тёмно-синяя дуга — край Земли.", "Mirando al Sol desde el satélite. El gran arco azul oscuro es el borde de la Tierra.", "从卫星看向太阳方向的样子。深蓝色的大圆弧是地球边缘。", "उपग्रह से सूर्य की ओर देखने पर दृश्य। बड़ा गहरा नीला चाप पृथ्वी का किनारा है।"],
"地球中心から見た様子です。": ["As seen from the centre of the Earth.", "Vu du centre de la Terre.", "Вид из центра Земли.", "Visto desde el centro de la Tierra.", "从地心看的样子。", "पृथ्वी के केंद्र से दृश्य।"],
"最大食の地点での見え方を計算中…": ["Computing the view at the point of greatest eclipse…", "Calcul de la vue au lieu du maximum…", "Расчёт вида в точке наибольшей фазы…", "Calculando la vista en el punto del máximo…", "正在计算食甚地点的情况…", "अधिकतम ग्रहण के स्थान का दृश्य गणना हो रहा है…"],
"北": ["N", "N", "С", "N", "北", "उ"],
"東": ["E", "E", "В", "E", "东", "पू"],
"天頂": ["Zenith", "Zénith", "Зенит", "Cenit", "天顶", "शिरोबिंदु"],
"地球": ["Earth", "Terre", "Земля", "Tierra", "地球", "पृथ्वी"],
"太陽の視直径 {v}′": ["Sun’s apparent diameter {v}′", "Diamètre apparent du Soleil {v}′", "Видимый диаметр Солнца {v}′", "Diámetro aparente del Sol {v}′", "太阳视直径 {v}′", "सूर्य का आभासी व्यास {v}′"],
"食なし": ["no eclipse", "pas d’éclipse", "затмения нет", "sin eclipse", "无食", "ग्रहण नहीं"],
"皆既中": ["totality", "totalité", "полная фаза", "totalidad", "全食中", "पूर्णता"],
"金環中": ["annularity", "annularité", "кольцеобразная фаза", "anularidad", "环食中", "वलय"],
"部分食中": ["partial phase", "phase partielle", "частная фаза", "fase parcial", "偏食中", "आंशिक चरण"],
"通過前後": ["outside the transit", "hors du transit", "вне прохождения", "fuera del tránsito", "凌日前后", "पारगमन के बाहर"],
"太陽面を通過中": ["crossing the Sun", "sur le disque solaire", "на диске Солнца", "sobre el disco solar", "正在凌日", "सूर्य बिंब पर"],
"太陽の縁にかかっている": ["on the limb of the Sun", "sur le bord du Soleil", "на краю Солнца", "en el borde del Sol", "位于日面边缘", "सूर्य के किनारे पर"],
"太陽中心からの距離": ["Distance from the Sun’s centre", "Distance au centre du Soleil", "Расстояние от центра Солнца", "Distancia al centro del Sol", "与日面中心的距离", "सूर्य के केंद्र से दूरी"],
"太陽の高度・方位": ["Sun altitude, azimuth", "Hauteur, azimut du Soleil", "Высота, азимут Солнца", "Altura y acimut del Sol", "太阳高度、方位角", "सूर्य की ऊँचाई, दिगंश"],
"衛星の高度": ["Satellite altitude", "Altitude du satellite", "Высота спутника", "Altitud del satélite", "卫星高度", "उपग्रह की ऊँचाई"],
"地球の縁からの太陽": ["Sun above the Earth’s limb", "Soleil au-dessus du bord terrestre", "Солнце над краем Земли", "Sol sobre el borde terrestre", "太阳离地球边缘", "पृथ्वी के किनारे से सूर्य"],
"地平線の下": ["below the horizon", "sous l’horizon", "под горизонтом", "bajo el horizonte", "在地平线下", "क्षितिज के नीचे"],
"地球に隠れている": ["hidden by the Earth", "caché par la Terre", "закрыто Землёй", "oculto por la Tierra", "被地球遮挡", "पृथ्वी से छिपा है"],
"食分と食面積率の変化": ["Magnitude and obscuration", "Magnitude et obscuration", "Фаза и доля закрытой площади", "Magnitud y oscurecimiento", "食分与食面积比的变化", "परिमाण और आच्छादन"],
"{body}の中心と太陽の中心の距離（″）": ["Distance between the centres of {body} and the Sun (″)", "Distance entre les centres de {body} et du Soleil (″)", "Расстояние между центрами планеты ({body}) и Солнца (″)", "Distancia entre los centros de {body} y el Sol (″)", "{body}中心与日面中心的距离（″）", "{body} और सूर्य के केंद्रों की दूरी (″)"],
"外接": ["external contact", "contact extérieur", "внешний контакт", "contacto externo", "外切", "बाह्य स्पर्श"],
"内接": ["internal contact", "contact intérieur", "внутренний контакт", "contacto interno", "内切", "आंतरिक स्पर्श"],
"太陽の高度（°）": ["Altitude of the Sun (°)", "Hauteur du Soleil (°)", "Высота Солнца (°)", "Altura del Sol (°)", "太阳高度（°）", "सूर्य की ऊँचाई (°)"],
"地平線": ["horizon", "horizon", "горизонт", "horizonte", "地平线", "क्षितिज"],
"地球の縁から太陽までの角度（°）— 0 未満は地球に隠される": ["Angle of the Sun above the Earth’s limb (°) — below 0 the Earth hides it", "Angle du Soleil au-dessus du bord de la Terre (°) — sous 0, la Terre le cache", "Угол Солнца над краем Земли (°) — ниже 0 Солнце закрыто Землёй", "Ángulo del Sol sobre el borde terrestre (°) — por debajo de 0 lo oculta la Tierra", "太阳与地球边缘的角距（°）— 小于 0 时被地球遮挡", "पृथ्वी के किनारे से सूर्य का कोण (°) — 0 से कम पर पृथ्वी उसे छिपाती है"],
"地球の縁からの離角": ["angle above the Earth’s limb", "angle au-dessus du bord terrestre", "угол над краем Земли", "ángulo sobre el borde terrestre", "与地球边缘的角距", "पृथ्वी के किनारे से कोण"],
"地球の縁": ["Earth’s limb", "bord de la Terre", "край Земли", "borde terrestre", "地球边缘", "पृथ्वी का किनारा"],
"シンプル地図（オフライン）": ["Simple map (offline)", "Carte simple (hors ligne)", "Простая карта (офлайн)", "Mapa simple (sin conexión)", "简易地图（离线）", "सरल मानचित्र (ऑफ़लाइन)"],
"詳細地図（OpenStreetMap）": ["Detailed map (OpenStreetMap)", "Carte détaillée (OpenStreetMap)", "Подробная карта (OpenStreetMap)", "Mapa detallado (OpenStreetMap)", "详细地图（OpenStreetMap）", "विस्तृत मानचित्र (OpenStreetMap)"],
"地図データを計算中…（数秒かかります）": ["Computing the map… (a few seconds)", "Calcul de la carte… (quelques secondes)", "Расчёт карты… (несколько секунд)", "Calculando el mapa… (unos segundos)", "正在计算地图数据…（需要几秒钟）", "मानचित्र की गणना हो रही है… (कुछ सेकंड)"],
"この現象は地上からは見られない（地球上に食が生じない）ため、食の地図はありません。": ["This event cannot be seen from the ground (there is no eclipse anywhere on Earth), so there is no map.", "Cet événement n’est pas visible depuis le sol (aucune éclipse sur Terre) : pas de carte.", "Это явление не видно с Земли (затмения на Земле нет), поэтому карты нет.", "Este evento no se ve desde el suelo (no hay eclipse en la Tierra), así que no hay mapa.", "此天象在地面上看不到（地球上不发生食），因此没有食的地图。", "यह घटना धरती से नहीं दिखती (पृथ्वी पर कहीं ग्रहण नहीं), इसलिए मानचित्र नहीं है।"],
"部分食が見える範囲の境界（太陽が地平線上）": ["Limit of the partial eclipse (Sun above the horizon)", "Limite de visibilité de l’éclipse partielle (Soleil au-dessus de l’horizon)", "Граница видимости частного затмения (Солнце над горизонтом)", "Límite de visibilidad del eclipse parcial (Sol sobre el horizonte)", "偏食可见范围的边界（太阳在地平线上）", "आंशिक ग्रहण दिखने की सीमा (सूर्य क्षितिज के ऊपर)"],
"最大食分 {v}": ["greatest magnitude {v}", "magnitude maximale {v}", "наибольшая фаза {v}", "magnitud máxima {v}", "最大食分 {v}", "अधिकतम परिमाण {v}"],
"部分食の北限界線": ["Northern limit of the partial eclipse", "Limite nord de l’éclipse partielle", "Северная граница частного затмения", "Límite norte del eclipse parcial", "偏食北界线", "आंशिक ग्रहण की उत्तरी सीमा"],
"部分食の南限界線": ["Southern limit of the partial eclipse", "Limite sud de l’éclipse partielle", "Южная граница частного затмения", "Límite sur del eclipse parcial", "偏食南界线", "आंशिक ग्रहण की दक्षिणी सीमा"],
"金環帯の北限界線": ["Northern limit of annularity", "Limite nord de l’annularité", "Северная граница кольцеобразной фазы", "Límite norte de la anularidad", "环食带北界线", "वलय पथ की उत्तरी सीमा"],
"金環帯の南限界線": ["Southern limit of annularity", "Limite sud de l’annularité", "Южная граница кольцеобразной фазы", "Límite sur de la anularidad", "环食带南界线", "वलय पथ की दक्षिणी सीमा"],
"皆既帯の北限界線": ["Northern limit of totality", "Limite nord de la totalité", "Северная граница полной фазы", "Límite norte de la totalidad", "全食带北界线", "पूर्णता पथ की उत्तरी सीमा"],
"皆既帯の南限界線": ["Southern limit of totality", "Limite sud de la totalité", "Южная граница полной фазы", "Límite sur de la totalidad", "全食带南界线", "पूर्णता पथ की दक्षिणी सीमा"],
"中心食帯の北限界線": ["Northern limit of the central path", "Limite nord de la bande centrale", "Северная граница центральной полосы", "Límite norte de la franja central", "中心食带北界线", "केंद्रीय पथ की उत्तरी सीमा"],
"中心食帯の南限界線": ["Southern limit of the central path", "Limite sud de la bande centrale", "Южная граница центральной полосы", "Límite sur de la franja central", "中心食带南界线", "केंद्रीय पथ की दक्षिणी सीमा"],
"中心線": ["Central line", "Ligne centrale", "Центральная линия", "Línea central", "中心线", "केंद्र रेखा"],
"{time} の本影": ["Umbra at {time}", "Ombre à {time}", "Тень в {time}", "Umbra a las {time}", "{time} 的本影", "{time} पर प्रच्छाया"],
"中心食の継続 {v}": ["central duration {v}", "durée centrale {v}", "центральная фаза {v}", "duración central {v}", "中心食持续 {v}", "केंद्रीय अवधि {v}"],
"最大食 {time}": ["Greatest eclipse {time}", "Maximum {time}", "Наибольшая фаза {time}", "Máximo {time}", "食甚 {time}", "अधिकतम ग्रहण {time}"],
"金環帯（黒線は限界線・10分ごとの本影）": ["Path of annularity (black lines: limits and the shadow every 10 min)", "Bande d’annularité (lignes noires : limites et ombre toutes les 10 min)", "Полоса кольцеобразной фазы (чёрные линии — границы и тень через каждые 10 мин)", "Franja de anularidad (líneas negras: límites y sombra cada 10 min)", "环食带（黑线为界线和每 10 分钟的本影）", "वलय पथ (काली रेखाएँ: सीमाएँ और हर 10 मिनट की छाया)"],
"皆既帯（黒線は限界線・10分ごとの本影）": ["Path of totality (black lines: limits and the shadow every 10 min)", "Bande de totalité (lignes noires : limites et ombre toutes les 10 min)", "Полоса полной фазы (чёрные линии — границы и тень через каждые 10 мин)", "Franja de totalidad (líneas negras: límites y sombra cada 10 min)", "全食带（黑线为界线和每 10 分钟的本影）", "पूर्णता पथ (काली रेखाएँ: सीमाएँ और हर 10 मिनट की छाया)"],
"部分食の限界線": ["Limits of the partial eclipse", "Limites de l’éclipse partielle", "Границы частного затмения", "Límites del eclipse parcial", "偏食界线", "आंशिक ग्रहण की सीमाएँ"],
"色は太陽が地平線上にある時間帯での最大食分。地図をクリックすると、その地点での見え方を計算します。": ["Colours show the greatest magnitude while the Sun is above the horizon. Click the map to compute the circumstances at that place.", "Les couleurs indiquent la magnitude maximale lorsque le Soleil est au-dessus de l’horizon. Cliquez sur la carte pour calculer les circonstances en ce lieu.", "Цвет — наибольшая фаза, пока Солнце над горизонтом. Щёлкните карту, чтобы рассчитать обстоятельства в этом месте.", "Los colores indican la magnitud máxima mientras el Sol está sobre el horizonte. Haga clic en el mapa para calcular las circunstancias en ese lugar.", "颜色表示太阳在地平线上时的最大食分。点击地图可计算该地点的情况。", "रंग सूर्य के क्षितिज के ऊपर रहते समय का अधिकतम परिमाण दिखाते हैं। उस स्थान की स्थिति जानने के लिए मानचित्र पर क्लिक करें।"],
"{label}の時刻に太陽が真上にある地点": ["Place with the Sun overhead at {label}", "Lieu où le Soleil est au zénith au {label}", "Точка, где Солнце в зените в момент: {label}", "Lugar con el Sol en el cenit en el {label}", "{label}时刻太阳直射的地点", "{label} के समय जहाँ सूर्य सिर के ठीक ऊपर है"],
"地心の接触時刻（{start}〜{end} {tz}）にもとづく概略図。地図をクリックすると、その地点での正確な時刻を計算します。": ["Sketch based on the geocentric contacts ({start}–{end} {tz}). Click the map to compute the exact times at that place.", "Schéma fondé sur les contacts géocentriques ({start}–{end} {tz}). Cliquez sur la carte pour calculer les heures exactes en ce lieu.", "Схема по геоцентрическим контактам ({start}–{end} {tz}). Щёлкните карту, чтобы рассчитать точное время в этом месте.", "Esquema basado en los contactos geocéntricos ({start}–{end} {tz}). Haga clic en el mapa para calcular las horas exactas en ese lugar.", "基于地心接触时刻（{start}～{end} {tz}）的示意图。点击地图可计算该地点的准确时刻。", "भूकेंद्रीय स्पर्शों ({start}–{end} {tz}) पर आधारित रेखाचित्र। उस स्थान का सटीक समय जानने के लिए मानचित्र पर क्लिक करें।"],
"観測地: {name}": ["Observer: {name}", "Lieu d’observation : {name}", "Место наблюдения: {name}", "Lugar de observación: {name}", "观测地：{name}", "प्रेक्षण स्थल: {name}"],
"現象中の衛星直下点の軌跡": ["Ground track of the satellite during the event", "Trace au sol du satellite pendant l’événement", "Трасса спутника во время явления", "Traza del satélite durante el evento", "天象期间的星下点轨迹", "घटना के दौरान उपग्रह का भू-पथ"],
"衛星高度 {v} km": ["satellite altitude {v} km", "altitude du satellite {v} km", "высота спутника {v} км", "altitud del satélite {v} km", "卫星高度 {v} km", "उपग्रह की ऊँचाई {v} किमी"],
"衛星直下点の軌跡": ["Ground track of the satellite", "Trace au sol du satellite", "Трасса спутника", "Traza del satélite", "星下点轨迹", "उपग्रह का भू-पथ"],
"この地点での見え方を計算中…": ["Computing the circumstances here…", "Calcul des circonstances en ce lieu…", "Расчёт обстоятельств в этом месте…", "Calculando las circunstancias aquí…", "正在计算此地点的情况…", "यहाँ की स्थिति की गणना हो रही है…"],
"地図上の地点 {pos}": ["Place on the map {pos}", "Lieu sur la carte {pos}", "Точка на карте {pos}", "Lugar en el mapa {pos}", "地图上的地点 {pos}", "मानचित्र पर स्थान {pos}"],
"この地点では食は起こりません。": ["No eclipse at this place.", "Pas d’éclipse en ce lieu.", "В этом месте затмения нет.", "No hay eclipse en este lugar.", "此地点不发生食。", "इस स्थान पर ग्रहण नहीं होगा।"],
"この地点では見られません。": ["Not visible from this place.", "Non visible depuis ce lieu.", "Из этого места не видно.", "No se ve desde este lugar.", "此地点看不到。", "इस स्थान से दिखाई नहीं देगा।"],
"全経過が見える": ["whole event visible", "visible en entier", "видно полностью", "visible completo", "全过程可见", "पूरी घटना दिखेगी"],
"一部が見える（{p}%）": ["partly visible ({p}%)", "visible en partie ({p} %)", "видно частично ({p}%)", "visible en parte ({p}%)", "部分可见（{p}%）", "आंशिक रूप से दिखेगी ({p}%)"],
"太陽が地平線の下で見えない": ["not visible, Sun below the horizon", "invisible, Soleil sous l’horizon", "не видно, Солнце под горизонтом", "no visible, Sol bajo el horizonte", "太阳在地平线下，看不到", "नहीं दिखेगी, सूर्य क्षितिज के नीचे"],
"始まり": ["Begins", "Début", "Начало", "Inicio", "开始", "आरंभ"],
"終わり": ["Ends", "Fin", "Конец", "Fin", "结束", "समाप्ति"],
"皆既": ["Totality", "Totalité", "Полная фаза", "Totalidad", "全食", "पूर्णता"],
"金環": ["Annularity", "Annularité", "Кольцеобразная фаза", "Anularidad", "环食", "वलय"],
"太陽高度(最大時)": ["Sun altitude (maximum)", "Hauteur du Soleil (maximum)", "Высота Солнца (максимум)", "Altura del Sol (máximo)", "太阳高度（最大时）", "सूर्य की ऊँचाई (अधिकतम पर)"],
"見え方": ["Visibility", "Visibilité", "Видимость", "Visibilidad", "可见情况", "दृश्यता"],
"この地点の詳細を表示": ["Show details for this place", "Détails pour ce lieu", "Подробности для этого места", "Detalles de este lugar", "显示此地点的详细信息", "इस स्थान का विवरण दिखाएँ"],
"幅 {v}": ["width {v}", "largeur {v}", "ширина {v}", "ancho {v}", "宽 {v}", "चौड़ाई {v}"],
"サロス {v}": ["Saros {v}", "saros {v}", "сарос {v}", "saros {v}", "沙罗 {v}", "सारोस {v}"],
"最大食の地点での接触時刻:": ["Contacts at the point of greatest eclipse:", "Contacts au lieu du maximum :", "Контакты в точке наибольшей фазы:", "Contactos en el punto del máximo:", "食甚地点的接触时刻：", "अधिकतम ग्रहण के स्थान पर स्पर्श:"],
"高度 {alt}° 方位 {az}°": ["alt. {alt}° az. {az}°", "haut. {alt}° az. {az}°", "выс. {alt}° аз. {az}°", "alt. {alt}° ac. {az}°", "高度 {alt}° 方位角 {az}°", "ऊँचाई {alt}° दिगंश {az}°"],
"衛星 {pos} {alt} km": ["satellite {pos} {alt} km", "satellite {pos} {alt} km", "спутник {pos} {alt} км", "satélite {pos} {alt} km", "卫星 {pos} {alt} km", "उपग्रह {pos} {alt} किमी"],
"（{n} 点の時系列 — CSV でダウンロードできます）": ["(time series of {n} points — download it as CSV)", "(série temporelle de {n} points — téléchargeable en CSV)", "(временной ряд из {n} точек — можно скачать в CSV)", "(serie temporal de {n} puntos — descárguela en CSV)", "（{n} 点的时间序列 — 可下载为 CSV）", "({n} बिंदुओं की समय श्रृंखला — CSV में डाउनलोड करें)"],
"コピーしました": ["Copied", "Copié", "Скопировано", "Copiado", "已复制", "कॉपी हो गया"],
"コピーできませんでした": ["Could not copy", "Copie impossible", "Не удалось скопировать", "No se pudo copiar", "无法复制", "कॉपी नहीं हो सका"],
"日本時間 (JST)": ["Japan (JST)", "Japon (JST)", "Япония (JST)", "Japón (JST)", "日本时间 (JST)", "जापान (JST)"],
"世界時 (UTC)": ["Universal Time (UTC)", "Temps universel (UTC)", "Всемирное время (UTC)", "Tiempo universal (UTC)", "世界时 (UTC)", "सार्वत्रिक समय (UTC)"],
"このPCの時刻": ["This computer’s time zone", "Fuseau de cet ordinateur", "Часовой пояс компьютера", "Zona horaria de este equipo", "本机时区", "इस कंप्यूटर का समय"],
"韓国": ["Korea", "Corée", "Корея", "Corea", "韩国", "कोरिया"],
"中国": ["China", "Chine", "Китай", "China", "中国", "चीन"],
"シンガポール": ["Singapore", "Singapour", "Сингапур", "Singapur", "新加坡", "सिंगापुर"],
"シドニー": ["Sydney", "Sydney", "Сидней", "Sídney", "悉尼", "सिडनी"],
"ハワイ": ["Hawaii", "Hawaï", "Гавайи", "Hawái", "夏威夷", "हवाई"],
"米国太平洋": ["US Pacific", "États-Unis, Pacifique", "США, тихоокеанское", "EE. UU., Pacífico", "美国太平洋时间", "अमेरिका प्रशांत"],
"米国中部": ["US Central", "États-Unis, Centre", "США, центральное", "EE. UU., Centro", "美国中部时间", "अमेरिका मध्य"],
"米国東部": ["US Eastern", "États-Unis, Est", "США, восточное", "EE. UU., Este", "美国东部时间", "अमेरिका पूर्वी"],
"英国": ["United Kingdom", "Royaume-Uni", "Великобритания", "Reino Unido", "英国", "ब्रिटेन"],
"中央ヨーロッパ": ["Central Europe", "Europe centrale", "Центральная Европа", "Europa central", "中欧", "मध्य यूरोप"],
"モスクワ": ["Moscow", "Moscou", "Москва", "Moscú", "莫斯科", "मॉस्को"],
"エジプト": ["Egypt", "Égypte", "Египет", "Egipto", "埃及", "मिस्र"],
"インド": ["India", "Inde", "Индия", "India", "印度", "भारत"],
"メキシコ": ["Mexico", "Mexique", "Мексика", "México", "墨西哥", "मेक्सिको"],
"実時間": ["Real time", "Temps réel", "Реальное время", "Tiempo real", "实时", "वास्तविक समय"],
"10倍速": ["10×", "10×", "10×", "10×", "10 倍速", "10×"],
"60倍速（1秒=1分）": ["60× (1 s = 1 min)", "60× (1 s = 1 min)", "60× (1 с = 1 мин)", "60× (1 s = 1 min)", "60 倍速（1 秒 = 1 分）", "60× (1 से. = 1 मि.)"],
"300倍速（1秒=5分）": ["300× (1 s = 5 min)", "300× (1 s = 5 min)", "300× (1 с = 5 мин)", "300× (1 s = 5 min)", "300 倍速（1 秒 = 5 分）", "300× (1 से. = 5 मि.)"],
"900倍速（1秒=15分）": ["900× (1 s = 15 min)", "900× (1 s = 15 min)", "900× (1 с = 15 мин)", "900× (1 s = 15 min)", "900 倍速（1 秒 = 15 分）", "900× (1 से. = 15 मि.)"],
"3600倍速（1秒=1時間）": ["3600× (1 s = 1 h)", "3600× (1 s = 1 h)", "3600× (1 с = 1 ч)", "3600× (1 s = 1 h)", "3600 倍速（1 秒 = 1 小时）", "3600× (1 से. = 1 घं.)"],
"日の出時にすでに通過中（終わりは見える）": ["Already in transit at sunrise (end visible)", "Transit en cours au lever du Soleil (fin visible)", "На восходе уже идёт (конец виден)", "Ya en tránsito al amanecer (final visible)", "日出时已在凌日（可见结束）", "सूर्योदय पर पहले से जारी (अंत दिखेगा)"],
"日の入り時に通過中（始まりは見える）": ["Still in transit at sunset (start visible)", "Transit en cours au coucher du Soleil (début visible)", "На закате ещё идёт (начало видно)", "Aún en tránsito al anochecer (inicio visible)", "日落时仍在凌日（可见开始）", "सूर्यास्त पर अभी जारी (आरंभ दिखेगा)"],
"途中の一部だけ見える": ["Only part of the middle visible", "Seule une partie du milieu est visible", "Видна только средняя часть", "Solo se ve una parte intermedia", "只能看到中间一部分", "केवल बीच का भाग दिखेगा"],
"始まりと終わりは見えるが途中で夜になる": ["Start and end visible, night in between", "Début et fin visibles, nuit entre les deux", "Начало и конец видны, в середине ночь", "Inicio y final visibles, de noche en medio", "可见开始和结束，但中途入夜", "आरंभ और अंत दिखेंगे, बीच में रात"],
"太陽同期 680km（計画）": ["Sun-synchronous 680 km (planned)", "Héliosynchrone 680 km (prévu)", "Солнечно-синхронная 680 км (проект)", "Heliosíncrona 680 km (prevista)", "太阳同步 680 km（计划）", "सूर्य-समकालिक 680 किमी (योजना)"],
"札幌": ["Sapporo", "Sapporo", "Саппоро", "Sapporo", "札幌", "साप्पोरो"],
"釧路": ["Kushiro", "Kushiro", "Кусиро", "Kushiro", "钏路", "कुशिरो"],
"仙台": ["Sendai", "Sendai", "Сендай", "Sendai", "仙台", "सेंदाई"],
"新潟": ["Niigata", "Niigata", "Ниигата", "Niigata", "新潟", "निइगाता"],
"宇都宮": ["Utsunomiya", "Utsunomiya", "Уцуномия", "Utsunomiya", "宇都宫", "उत्सुनोमिया"],
"長野": ["Nagano", "Nagano", "Нагано", "Nagano", "长野", "नागानो"],
"富山": ["Toyama", "Toyama", "Тояма", "Toyama", "富山", "तोयामा"],
"金沢": ["Kanazawa", "Kanazawa", "Канадзава", "Kanazawa", "金泽", "कानाज़ावा"],
"横浜": ["Yokohama", "Yokohama", "Иокогама", "Yokohama", "横滨", "योकोहामा"],
"名古屋": ["Nagoya", "Nagoya", "Нагоя", "Nagoya", "名古屋", "नागोया"],
"京都": ["Kyoto", "Kyoto", "Киото", "Kioto", "京都", "क्योटो"],
"大阪": ["Osaka", "Osaka", "Осака", "Osaka", "大阪", "ओसाका"],
"広島": ["Hiroshima", "Hiroshima", "Хиросима", "Hiroshima", "广岛", "हिरोशिमा"],
"高知": ["Kōchi", "Kōchi", "Коти", "Kōchi", "高知", "कोची (जापान)"],
"福岡": ["Fukuoka", "Fukuoka", "Фукуока", "Fukuoka", "福冈", "फ़ुकुओका"],
"鹿児島": ["Kagoshima", "Kagoshima", "Кагосима", "Kagoshima", "鹿儿岛", "कागोशिमा"],
"那覇": ["Naha", "Naha", "Наха", "Naha", "那霸", "नाहा"],
"石垣島": ["Ishigaki Island", "Île d’Ishigaki", "Остров Исигаки", "Isla Ishigaki", "石垣岛", "इशिगाकी द्वीप"],
"ソウル": ["Seoul", "Séoul", "Сеул", "Seúl", "首尔", "सियोल"],
"北京": ["Beijing", "Pékin", "Пекин", "Pekín", "北京", "बीजिंग"],
"上海": ["Shanghai", "Shanghai", "Шанхай", "Shanghái", "上海", "शंघाई"],
"台北": ["Taipei", "Taipei", "Тайбэй", "Taipéi", "台北", "ताइपे"],
"ホノルル": ["Honolulu", "Honolulu", "Гонолулу", "Honolulu", "檀香山", "होनोलूलू"],
"ロサンゼルス": ["Los Angeles", "Los Angeles", "Лос-Анджелес", "Los Ángeles", "洛杉矶", "लॉस एंजिल्स"],
"ダラス": ["Dallas", "Dallas", "Даллас", "Dallas", "达拉斯", "डलास"],
"マサトラン (メキシコ)": ["Mazatlán (Mexico)", "Mazatlán (Mexique)", "Масатлан (Мексика)", "Mazatlán (México)", "马萨特兰（墨西哥）", "माज़ातलान (मेक्सिको)"],
"ニューヨーク": ["New York", "New York", "Нью-Йорк", "Nueva York", "纽约", "न्यूयॉर्क"],
"サンパウロ": ["São Paulo", "São Paulo", "Сан-Паулу", "São Paulo", "圣保罗", "साओ पाउलो"],
"レイキャビク": ["Reykjavík", "Reykjavik", "Рейкьявик", "Reikiavik", "雷克雅未克", "रेक्याविक"],
"ロンドン": ["London", "Londres", "Лондон", "Londres", "伦敦", "लंदन"],
"パリ": ["Paris", "Paris", "Париж", "París", "巴黎", "पेरिस"],
"マドリード": ["Madrid", "Madrid", "Мадрид", "Madrid", "马德里", "मैड्रिड"],
"カイロ": ["Cairo", "Le Caire", "Каир", "El Cairo", "开罗", "काहिरा"],
"ルクソール (エジプト)": ["Luxor (Egypt)", "Louxor (Égypte)", "Луксор (Египет)", "Luxor (Egipto)", "卢克索（埃及）", "लक्सर (मिस्र)"],
"国際宇宙ステーション ISS": ["International Space Station ISS", "Station spatiale internationale ISS", "Международная космическая станция МКС", "Estación Espacial Internacional ISS", "国际空间站 ISS", "अंतर्राष्ट्रीय अंतरिक्ष स्टेशन ISS"],
"ハッブル宇宙望遠鏡": ["Hubble Space Telescope", "Télescope spatial Hubble", "Космический телескоп «Хаббл»", "Telescopio espacial Hubble", "哈勃空间望远镜", "हबल अंतरिक्ष दूरबीन"],
"中国宇宙ステーション 天宮": ["Chinese space station Tiangong", "Station spatiale chinoise Tiangong", "Китайская космическая станция «Тяньгун»", "Estación espacial china Tiangong", "中国空间站 天宫", "चीनी अंतरिक्ष स्टेशन तियांगोंग"],
"ひまわり9号 (静止 140.7°E)": ["Himawari-9 (geostationary 140.7°E)", "Himawari-9 (géostationnaire 140,7° E)", "«Химавари-9» (геостационарный, 140,7° в. д.)", "Himawari-9 (geoestacionario 140,7° E)", "向日葵 9 号（静止 140.7°E）", "हिमावारी-9 (भूस्थिर 140.7° पू.)"],
"ひまわり9号": ["Himawari-9", "Himawari-9", "«Химавари-9»", "Himawari-9", "向日葵 9 号", "हिमावारी-9"],
"GOES-19 (静止 75.2°W)": ["GOES-19 (geostationary 75.2°W)", "GOES-19 (géostationnaire 75,2° O)", "GOES-19 (геостационарный, 75,2° з. д.)", "GOES-19 (geoestacionario 75,2° O)", "GOES-19（静止 75.2°W）", "GOES-19 (भूस्थिर 75.2° प.)"],
"太陽同期軌道 高度700km・昇交点18時 (仮想)": ["Sun-synchronous orbit 700 km, ascending node 18:00 (hypothetical)", "Orbite héliosynchrone 700 km, nœud ascendant 18 h (fictive)", "Солнечно-синхронная орбита 700 км, восходящий узел 18:00 (условная)", "Órbita heliosíncrona 700 km, nodo ascendente 18:00 (hipotética)", "太阳同步轨道 高度 700 km，升交点 18 时（假想）", "सूर्य-समकालिक कक्षा 700 किमी, आरोही पात 18:00 (काल्पनिक)"],
"太陽同期 700km": ["Sun-synchronous 700 km", "Héliosynchrone 700 km", "Солнечно-синхронная 700 км", "Heliosíncrona 700 km", "太阳同步 700 km", "सूर्य-समकालिक 700 किमी"],
"準天頂軌道 (仮想・みちびき型)": ["Quasi-zenith orbit (hypothetical, Michibiki type)", "Orbite quasi zénithale (fictive, type Michibiki)", "Квазизенитная орбита (условная, типа «Митибики»)", "Órbita cuasicenital (hipotética, tipo Michibiki)", "准天顶轨道（假想，引路型）", "अर्ध-शिरोबिंदु कक्षा (काल्पनिक, मिचिबिकी प्रकार)"],
"準天頂軌道": ["Quasi-zenith orbit", "Orbite quasi zénithale", "Квазизенитная орбита", "Órbita cuasicenital", "准天顶轨道", "अर्ध-शिरोबिंदु कक्षा"],
"モルニヤ軌道 (仮想)": ["Molniya orbit (hypothetical)", "Orbite de Molniya (fictive)", "Орбита «Молния» (условная)", "Órbita Molniya (hipotética)", "闪电轨道（假想）", "मोलनिया कक्षा (काल्पनिक)"],
"モルニヤ軌道": ["Molniya orbit", "Orbite de Molniya", "Орбита «Молния»", "Órbita Molniya", "闪电轨道", "मोलनिया कक्षा"],
"ジェイムズ・ウェッブ宇宙望遠鏡 (Horizons -170)": ["James Webb Space Telescope (Horizons -170)", "Télescope spatial James Webb (Horizons -170)", "Космический телескоп «Джеймс Уэбб» (Horizons -170)", "Telescopio espacial James Webb (Horizons -170)", "詹姆斯·韦布空间望远镜（Horizons -170）", "जेम्स वेब अंतरिक्ष दूरबीन (Horizons -170)"],
"太陽観測衛星ひので (SSCWeb の過去軌道)": ["Solar observatory Hinode (past orbit from SSCWeb)", "Observatoire solaire Hinode (orbite passée de SSCWeb)", "Солнечная обсерватория «Хиноде» (прошлая орбита из SSCWeb)", "Observatorio solar Hinode (órbita pasada de SSCWeb)", "太阳观测卫星 日出（SSCWeb 的历史轨道）", "सौर वेधशाला हिनोडे (SSCWeb की पिछली कक्षा)"],
"ひので": ["Hinode", "Hinode", "«Хиноде»", "Hinode", "日出卫星", "हिनोडे"],
"日食・太陽面通過 精密計算機": ["Precise Calculator of Solar Eclipses and Transits", "Calculateur précis d’éclipses de Soleil et de transits", "Точный калькулятор солнечных затмений и прохождений", "Calculadora precisa de eclipses de Sol y tránsitos", "日食与凌日精密计算器", "सूर्य ग्रहण और पारगमन का सटीक कैलकुलेटर"],
"JPL DE440 暦にもとづく高精度計算 ・ 地上の地点と人工衛星からの見え方": ["High-precision computation with the JPL DE440 ephemeris · as seen from places on the ground and from satellites", "Calcul de haute précision avec l’éphéméride JPL DE440 · vu depuis le sol et depuis des satellites", "Высокоточный расчёт по эфемеридам JPL DE440 · вид с Земли и со спутников", "Cálculo de alta precisión con la efeméride JPL DE440 · visto desde la superficie y desde satélites", "基于 JPL DE440 历表的高精度计算 · 地面地点与人造卫星的观测情况", "JPL DE440 एफ़ेमेरिस से उच्च-सटीक गणना · धरती के स्थानों और उपग्रहों से दृश्य"],
"言語 / Language": ["Language", "Langue / Language", "Язык / Language", "Idioma / Language", "语言 / Language", "भाषा / Language"],
"表示する時刻": ["Time zone", "Fuseau horaire", "Часовой пояс", "Zona horaria", "显示时区", "समय क्षेत्र"],
"保存した結果を開く": ["Open saved results", "Ouvrir des résultats enregistrés", "Открыть сохранённые результаты", "Abrir resultados guardados", "打开保存的结果", "सहेजे गए परिणाम खोलें"],
"使い方・用語": ["Help & terms", "Aide et termes", "Справка и термины", "Ayuda y términos", "用法与术语", "सहायता और शब्दावली"],
"終了": ["Quit", "Quitter", "Выход", "Salir", "退出", "बंद करें"],
"計算設定": ["Computation settings", "Paramètres de calcul", "Параметры расчёта", "Parámetros de cálculo", "计算设置", "गणना सेटिंग"],
"計算する現象": ["Phenomena to compute", "Phénomènes à calculer", "Явления для расчёта", "Fenómenos a calcular", "要计算的天象", "गणना की जाने वाली घटनाएँ"],
"どこから見るか（観測者）": ["Where to observe from (observer)", "Lieu d’observation (observateur)", "Откуда наблюдать (наблюдатель)", "Desde dónde observar (observador)", "从哪里看（观测者）", "कहाँ से देखें (प्रेक्षक)"],
"地上の地点": ["Place on Earth", "Lieu sur Terre", "Место на Земле", "Lugar en la Tierra", "地面地点", "धरती पर स्थान"],
"人工衛星": ["Satellite", "Satellite", "Спутник", "Satélite", "人造卫星", "उपग्रह"],
"よく使う地点": ["Common places", "Lieux courants", "Частые места", "Lugares habituales", "常用地点", "सामान्य स्थान"],
"― 一覧から選ぶ ―": ["— choose from the list —", "— choisir dans la liste —", "— выбрать из списка —", "— elegir de la lista —", "— 从列表中选择 —", "— सूची से चुनें —"],
"緯度 (°)": ["Latitude (°)", "Latitude (°)", "Широта (°)", "Latitud (°)", "纬度 (°)", "अक्षांश (°)"],
"北緯 +／南緯 −": ["north +, south −", "nord +, sud −", "северная +, южная −", "norte +, sur −", "北纬 +／南纬 −", "उत्तर +, दक्षिण −"],
"経度 (°)": ["Longitude (°)", "Longitude (°)", "Долгота (°)", "Longitud (°)", "经度 (°)", "देशांतर (°)"],
"東経 +／西経 −": ["east +, west −", "est +, ouest −", "восточная +, западная −", "este +, oeste −", "东经 +／西经 −", "पूर्व +, पश्चिम −"],
"標高 (m)": ["Elevation (m)", "Altitude (m)", "Высота над уровнем моря (м)", "Altitud (m)", "海拔 (m)", "ऊँचाई (मी)"],
"地点名（任意）": ["Place name (optional)", "Nom du lieu (facultatif)", "Название места (необязательно)", "Nombre del lugar (opcional)", "地点名称（可选）", "स्थान का नाम (वैकल्पिक)"],
"🗺 地図をクリックして地点を選ぶ": ["🗺 Pick a place on the map", "🗺 Choisir un lieu sur la carte", "🗺 Выбрать место на карте", "🗺 Elegir un lugar en el mapa", "🗺 点击地图选择地点", "🗺 मानचित्र पर स्थान चुनें"],
"プリセット": ["Preset", "Préréglage", "Шаблон", "Predefinido", "预设", "पूर्व-निर्धारित"],
"― 衛星を選ぶ ―": ["— choose a satellite —", "— choisir un satellite —", "— выбрать спутник —", "— elegir un satélite —", "— 选择卫星 —", "— उपग्रह चुनें —"],
"NORAD番号": ["NORAD ID", "N° NORAD", "Номер NORAD", "N.º NORAD", "NORAD 编号", "NORAD संख्या"],
"TLE貼付": ["Paste TLE", "Coller un TLE", "Вставить TLE", "Pegar TLE", "粘贴 TLE", "TLE चिपकाएँ"],
"静止衛星": ["Geostationary", "Géostationnaire", "Геостационарный", "Geoestacionario", "静止卫星", "भूस्थिर"],
"NORAD カタログ番号": ["NORAD catalogue number", "Numéro de catalogue NORAD", "Номер в каталоге NORAD", "Número de catálogo NORAD", "NORAD 编目号", "NORAD कैटलॉग संख्या"],
"最新TLEを取得": ["Get latest TLE", "Obtenir le dernier TLE", "Получить свежий TLE", "Obtener el último TLE", "获取最新 TLE", "नवीनतम TLE लें"],
"CelesTrak から最新の軌道要素（TLE）を取得して SGP4 で軌道を計算します。": ["Gets the latest orbital elements (TLE) from CelesTrak and computes the orbit with SGP4.", "Récupère les derniers éléments orbitaux (TLE) sur CelesTrak et calcule l’orbite avec SGP4.", "Свежие элементы орбиты (TLE) берутся из CelesTrak, орбита рассчитывается по SGP4.", "Obtiene los últimos elementos orbitales (TLE) de CelesTrak y calcula la órbita con SGP4.", "从 CelesTrak 获取最新轨道根数（TLE），用 SGP4 计算轨道。", "CelesTrak से नवीनतम कक्षीय तत्व (TLE) लेकर SGP4 से कक्षा की गणना करता है।"],
"TLE（2行または名前付き3行）": ["TLE (2 lines, or 3 with a name)", "TLE (2 lignes, ou 3 avec un nom)", "TLE (2 строки или 3 с названием)", "TLE (2 líneas, o 3 con nombre)", "TLE（2 行或带名称的 3 行）", "TLE (2 पंक्तियाँ, या नाम सहित 3)"],
"高度・太陽同期で指定": ["By altitude / sun-synchronous", "Par altitude / héliosynchrone", "По высоте / солнечно-синхронная", "Por altitud / heliosíncrona", "按高度·太阳同步指定", "ऊँचाई / सूर्य-समकालिक से"],
"軌道6要素を入力": ["Six orbital elements", "Six éléments orbitaux", "Шесть элементов орбиты", "Seis elementos orbitales", "输入六个轨道根数", "छह कक्षीय तत्व"],
"元期（軌道情報の日時, UTC）": ["Epoch (date of the orbit, UTC)", "Époque (date de l’orbite, UTC)", "Эпоха (дата орбиты, UTC)", "Época (fecha de la órbita, UTC)", "历元（轨道信息的日期时间，UTC）", "युग (कक्षा की तिथि, UTC)"],
"この時刻での軌道です（打ち上げ前なら軌道投入の予定日など）": ["The orbit at this time (before launch, e.g. the planned date of orbit insertion)", "L’orbite à cet instant (avant le lancement : date prévue de mise en orbite, par ex.)", "Орбита на этот момент (до запуска — например, плановая дата выведения)", "La órbita en este instante (antes del lanzamiento, p. ej. la fecha prevista de inserción)", "此时刻的轨道（发射前可填计划入轨日期等）", "इस समय की कक्षा (प्रक्षेपण से पहले, जैसे कक्षा में पहुँचने की योजनाबद्ध तिथि)"],
"近地点高度 (km)": ["Perigee altitude (km)", "Altitude du périgée (km)", "Высота перигея (км)", "Altitud del perigeo (km)", "近地点高度 (km)", "उपभू ऊँचाई (किमी)"],
"遠地点高度 (km)": ["Apogee altitude (km)", "Altitude de l’apogée (km)", "Высота апогея (км)", "Altitud del apogeo (km)", "远地点高度 (km)", "अपभू ऊँचाई (किमी)"],
"軌道長半径 (km)": ["Semi-major axis (km)", "Demi-grand axe (km)", "Большая полуось (км)", "Semieje mayor (km)", "半长轴 (km)", "अर्ध-दीर्घ अक्ष (किमी)"],
"軌道離心率": ["Eccentricity", "Excentricité", "Эксцентриситет", "Excentricidad", "偏心率", "उत्केंद्रता"],
"0 で円軌道（0 以上 1 未満）": ["0 for a circular orbit (0 ≤ e < 1)", "0 pour une orbite circulaire (0 ≤ e < 1)", "0 — круговая орбита (0 ≤ e < 1)", "0 para una órbita circular (0 ≤ e < 1)", "0 为圆轨道（0 ≤ e < 1）", "वृत्ताकार कक्षा के लिए 0 (0 ≤ e < 1)"],
"太陽同期軌道（傾斜角を高度から自動で決める）": ["Sun-synchronous orbit (inclination set from the altitude)", "Orbite héliosynchrone (inclinaison déduite de l’altitude)", "Солнечно-синхронная орбита (наклонение по высоте)", "Órbita heliosíncrona (inclinación según la altitud)", "太阳同步轨道（根据高度自动确定倾角）", "सूर्य-समकालिक कक्षा (झुकाव ऊँचाई से तय)"],
"軌道傾斜角 (°)": ["Inclination (°)", "Inclinaison (°)", "Наклонение (°)", "Inclinación (°)", "轨道倾角 (°)", "कक्षीय झुकाव (°)"],
"軌道面の指定": ["Orbital plane given by", "Plan orbital défini par", "Плоскость орбиты задаётся", "Plano orbital dado por", "轨道面的指定", "कक्षा तल का निर्धारण"],
"昇交点の地方時": ["Local time of ascending node", "Heure locale du nœud ascendant", "Местное время восходящего узла", "Hora local del nodo ascendente", "升交点地方时", "आरोही पात का स्थानीय समय"],
"昇交点赤経": ["Right ascension of ascending node", "Ascension droite du nœud ascendant", "Прямое восхождение восходящего узла", "Ascensión recta del nodo ascendente", "升交点赤经", "आरोही पात का विषुवांश"],
"平均太陽時。18:00 は夕方側で北上": ["Mean solar time; 18:00 means northbound on the evening side", "Temps solaire moyen ; 18:00 = vers le nord du côté du soir", "Среднее солнечное время; 18:00 — к северу на вечерней стороне", "Tiempo solar medio; 18:00 = hacia el norte por el lado de la tarde", "平太阳时。18:00 表示在傍晚一侧向北运行", "माध्य सौर समय; 18:00 का अर्थ शाम की ओर उत्तर दिशा में"],
"昇交点赤経 (°)": ["Right ascension of ascending node (°)", "Ascension droite du nœud ascendant (°)", "Прямое восхождение восходящего узла (°)", "Ascensión recta del nodo ascendente (°)", "升交点赤经 (°)", "आरोही पात का विषुवांश (°)"],
"近地点引数 (°)": ["Argument of perigee (°)", "Argument du périgée (°)", "Аргумент перигея (°)", "Argumento del perigeo (°)", "近地点幅角 (°)", "उपभू कोणांक (°)"],
"平均近点角 (°)": ["Mean anomaly (°)", "Anomalie moyenne (°)", "Средняя аномалия (°)", "Anomalía media (°)", "平近点角 (°)", "माध्य असंगति (°)"],
"元期に衛星が軌道上のどこにいるか": ["where the satellite is along its orbit at the epoch", "position du satellite sur son orbite à l’époque", "положение спутника на орбите на эпоху", "dónde está el satélite en su órbita en la época", "历元时卫星在轨道上的位置", "युग पर उपग्रह कक्षा में कहाँ है"],
"地球の扁平(J2)による軌道面の歳差を考慮": ["Include the precession of the orbital plane from the Earth’s oblateness (J2)", "Inclure la précession du plan orbital due à l’aplatissement terrestre (J2)", "Учитывать прецессию плоскости орбиты из-за сжатия Земли (J2)", "Incluir la precesión del plano orbital por el achatamiento terrestre (J2)", "考虑地球扁率（J2）引起的轨道面进动", "पृथ्वी के चपटेपन (J2) से कक्षा तल का पुरस्सरण शामिल करें"],
"平均近点角を変えて一括計算（打ち上げ前の検討）": ["Compute for many mean anomalies (planning before launch)", "Calculer pour plusieurs anomalies moyennes (étude avant lancement)", "Расчёт для разных средних аномалий (анализ до запуска)", "Calcular para varias anomalías medias (estudio antes del lanzamiento)", "改变平近点角批量计算（发射前的研究）", "कई माध्य असंगतियों के लिए गणना (प्रक्षेपण से पहले की योजना)"],
"平均近点角の刻み": ["Step of the mean anomaly", "Pas de l’anomalie moyenne", "Шаг средней аномалии", "Paso de la anomalía media", "平近点角步长", "माध्य असंगति का अंतराल"],
"5°（72 通り）": ["5° (72 cases)", "5° (72 cas)", "5° (72 варианта)", "5° (72 casos)", "5°（72 种）", "5° (72 स्थितियाँ)"],
"10°（36 通り）": ["10° (36 cases)", "10° (36 cas)", "10° (36 вариантов)", "10° (36 casos)", "10°（36 种）", "10° (36 स्थितियाँ)"],
"15°（24 通り）": ["15° (24 cases)", "15° (24 cas)", "15° (24 варианта)", "15° (24 casos)", "15°（24 种）", "15° (24 स्थितियाँ)"],
"30°（12 通り）": ["30° (12 cases)", "30° (12 cas)", "30° (12 вариантов)", "30° (12 casos)", "30°（12 种）", "30° (12 स्थितियाँ)"],
"45°（8 通り）": ["45° (8 cases)", "45° (8 cas)", "45° (8 вариантов)", "45° (8 casos)", "45°（8 种）", "45° (8 स्थितियाँ)"],
"衛星が軌道上のどこにいても起こりうる結果の範囲を、現象ごとにまとめます（期間は 1 年以内）。": ["Summarizes, for each event, the range of results wherever the satellite is along its orbit (period of one year at most).", "Résume, pour chaque événement, l’éventail des résultats quelle que soit la position du satellite sur son orbite (période d’un an au plus).", "Для каждого явления сводится диапазон результатов при любом положении спутника на орбите (период не более 1 года).", "Resume, para cada evento, el rango de resultados esté donde esté el satélite en su órbita (periodo de un año como máximo).", "按天象汇总卫星位于轨道上任意位置时可能出现的结果范围（期间须在 1 年以内）。", "हर घटना के लिए, उपग्रह कक्षा में कहीं भी हो, संभावित परिणामों की सीमा बताता है (अवधि अधिकतम 1 वर्ष)।"],
"GCRS（J2000赤道）基準の平均要素。計画中・仮想の衛星の検討向けです。": ["Mean elements referred to the GCRS (J2000 equator), for studying planned or hypothetical satellites.", "Éléments moyens rapportés au GCRS (équateur J2000), pour étudier des satellites prévus ou fictifs.", "Средние элементы в системе GCRS (экватор J2000) — для анализа проектируемых и условных спутников.", "Elementos medios referidos al GCRS (ecuador J2000), para estudiar satélites previstos o hipotéticos.", "以 GCRS（J2000 赤道）为基准的平均根数，用于研究计划中或假想的卫星。", "GCRS (J2000 विषुवत) के सापेक्ष माध्य तत्व, योजनाबद्ध या काल्पनिक उपग्रहों के अध्ययन के लिए।"],
"TLE の要素は SGP4 専用の平均要素（TEME 基準）なので、「TLE貼付」から入力してください。": ["TLE elements are SGP4 mean elements (TEME frame): enter them under “Paste TLE”.", "Les éléments d’un TLE sont des éléments moyens propres à SGP4 (repère TEME) : saisissez-les dans « Coller un TLE ».", "Элементы TLE — средние элементы SGP4 (система TEME): вводите их через «Вставить TLE».", "Los elementos de un TLE son elementos medios propios de SGP4 (sistema TEME): introdúzcalos en «Pegar TLE».", "TLE 的根数是 SGP4 专用的平均根数（TEME 基准），请在“粘贴 TLE”中输入。", "TLE के तत्व SGP4 के माध्य तत्व (TEME ढाँचा) हैं: इन्हें “TLE चिपकाएँ” में दर्ज करें।"],
"静止位置の経度 (°)": ["Longitude of the slot (°)", "Longitude de la position (°)", "Долгота точки стояния (°)", "Longitud de la posición (°)", "定点经度 (°)", "स्थिति का देशांतर (°)"],
"東経 +／西経 −（高度 35,786 km の理想静止軌道）": ["east +, west − (ideal geostationary orbit at 35,786 km)", "est +, ouest − (orbite géostationnaire idéale à 35 786 km)", "восточная +, западная − (идеальная геостационарная орбита на 35 786 км)", "este +, oeste − (órbita geoestacionaria ideal a 35 786 km)", "东经 +／西经 −（高度 35,786 km 的理想静止轨道）", "पूर्व +, पश्चिम − (35,786 किमी पर आदर्श भूस्थिर कक्षा)"],
"位置の刻み (分)": ["Step (min)", "Pas (min)", "Шаг (мин)", "Paso (min)", "位置步长（分）", "अंतराल (मि.)"],
"JPL Horizons の探査機・衛星の精密軌道を使います（例: -170 JWST、-21 SOHO、-125544 ISS）。地球近傍の衛星は刻みを 1〜2 分に。": ["Uses the precise orbit of a spacecraft or satellite from JPL Horizons (e.g. -170 JWST, -21 SOHO, -125544 ISS). For satellites near the Earth use a step of 1–2 min.", "Utilise l’orbite précise d’une sonde ou d’un satellite fournie par JPL Horizons (ex. : -170 JWST, -21 SOHO, -125544 ISS). Pour les satellites proches de la Terre, prenez un pas de 1 à 2 min.", "Используется точная орбита аппарата из JPL Horizons (например, -170 JWST, -21 SOHO, -125544 МКС). Для околоземных спутников задайте шаг 1–2 мин.", "Usa la órbita precisa de una sonda o satélite de JPL Horizons (p. ej., -170 JWST, -21 SOHO, -125544 ISS). Para satélites cercanos a la Tierra use un paso de 1–2 min.", "使用 JPL Horizons 的探测器·卫星精密轨道（例：-170 JWST、-21 SOHO、-125544 ISS）。近地卫星请将步长设为 1～2 分钟。", "JPL Horizons से अंतरिक्ष यान या उपग्रह की सटीक कक्षा लेता है (उदा. -170 JWST, -21 SOHO, -125544 ISS)। पृथ्वी के निकट उपग्रहों के लिए 1–2 मि. का अंतराल रखें।"],
"SSCWeb の衛星 ID": ["SSCWeb satellite ID", "Identifiant SSCWeb du satellite", "Идентификатор спутника SSCWeb", "ID de satélite de SSCWeb", "SSCWeb 卫星 ID", "SSCWeb उपग्रह ID"],
"NASA SSCWeb（衛星状況センター）が公開している科学衛星の過去の軌道（おもに 1 分間隔）を使います。CelesTrak からは最新の TLE しか取れないため、過去に衛星から見えた日食・太陽面通過を調べるときに使います（例: hinode ひので、iris、iss）。軌道データのない期間は自動的に除きます。": ["Uses past orbits of science satellites published by NASA SSCWeb (Satellite Situation Center), mostly every minute. CelesTrak gives only the latest TLE, so use this to look into eclipses and transits seen from a satellite in the past (e.g. hinode, iris, iss). Periods without orbit data are skipped automatically.", "Utilise les orbites passées de satellites scientifiques publiées par NASA SSCWeb (Satellite Situation Center), en général toutes les minutes. CelesTrak ne fournit que le dernier TLE : utilisez ceci pour étudier les éclipses et transits vus d’un satellite dans le passé (ex. : hinode, iris, iss). Les périodes sans données d’orbite sont ignorées automatiquement.", "Используются прошлые орбиты научных спутников из NASA SSCWeb (Satellite Situation Center), обычно с шагом 1 мин. CelesTrak даёт только свежий TLE, поэтому этот режим нужен для затмений и прохождений, наблюдавшихся со спутника в прошлом (например, hinode, iris, iss). Периоды без данных об орбите пропускаются автоматически.", "Usa órbitas pasadas de satélites científicos publicadas por NASA SSCWeb (Satellite Situation Center), en general cada minuto. CelesTrak solo da el último TLE, así que use esto para estudiar eclipses y tránsitos vistos desde un satélite en el pasado (p. ej., hinode, iris, iss). Los periodos sin datos de órbita se omiten automáticamente.", "使用 NASA SSCWeb（卫星态势中心）公开的科学卫星历史轨道（多为 1 分钟间隔）。CelesTrak 只能获取最新 TLE，因此要查过去从卫星上看到的日食·凌日时使用此项（例：hinode 日出卫星、iris、iss）。没有轨道数据的期间会自动排除。", "NASA SSCWeb (Satellite Situation Center) द्वारा प्रकाशित वैज्ञानिक उपग्रहों की पिछली कक्षाएँ (प्रायः हर मिनट) इस्तेमाल करता है। CelesTrak केवल नवीनतम TLE देता है, इसलिए अतीत में किसी उपग्रह से दिखे ग्रहण और पारगमन जानने के लिए इसका उपयोग करें (उदा. hinode, iris, iss)। जिन अवधियों का कक्षा डेटा नहीं है, वे अपने-आप छोड़ दी जाती हैं।"],
"衛星名（任意）": ["Satellite name (optional)", "Nom du satellite (facultatif)", "Название спутника (необязательно)", "Nombre del satélite (opcional)", "卫星名称（可选）", "उपग्रह का नाम (वैकल्पिक)"],
"地球上のどこかで見られる日食をすべて探し、皆既帯・金環帯の経路や部分食が見える範囲を地図で表示します。 太陽面通過は地球中心から見た接触時刻（世界共通の標準値）を計算します。地図をクリックすると、その地点での見え方を計算できます。": ["Finds every solar eclipse visible somewhere on Earth and maps the paths of totality and annularity and the area where the partial eclipse is seen. For transits it computes the contacts seen from the centre of the Earth (the standard values for everyone). Click the map to compute the circumstances at that place.", "Recherche toutes les éclipses de Soleil visibles quelque part sur Terre et cartographie les bandes de totalité et d’annularité ainsi que la zone de visibilité de l’éclipse partielle. Pour les transits, calcule les contacts vus du centre de la Terre (valeurs de référence communes). Cliquez sur la carte pour calculer les circonstances en un lieu.", "Находит все солнечные затмения, видимые где-либо на Земле, и показывает на карте полосы полной и кольцеобразной фаз и область видимости частного затмения. Для прохождений рассчитываются контакты из центра Земли (общие стандартные значения). Щёлкните карту, чтобы рассчитать обстоятельства в выбранном месте.", "Busca todos los eclipses de Sol visibles en algún lugar de la Tierra y muestra en el mapa las franjas de totalidad y anularidad y la zona donde se ve el eclipse parcial. Para los tránsitos calcula los contactos vistos desde el centro de la Tierra (valores estándar comunes). Haga clic en el mapa para calcular las circunstancias en un lugar.", "查找地球上某处可见的所有日食，并在地图上显示全食带·环食带的路径及偏食可见范围。凌日则计算从地心看的接触时刻（全球通用的标准值）。点击地图即可计算该地点的情况。", "पृथ्वी पर कहीं भी दिखने वाले सभी सूर्य ग्रहण खोजता है और पूर्णता व वलय पथ तथा आंशिक ग्रहण दिखने का क्षेत्र मानचित्र पर दिखाता है। पारगमन के लिए पृथ्वी के केंद्र से स्पर्श समय (सबके लिए मानक मान) की गणना करता है। किसी स्थान की स्थिति जानने के लिए मानचित्र पर क्लिक करें।"],
"期間": ["Period", "Période", "Период", "Periodo", "期间", "अवधि"],
"（UTC の日付）": ["(dates in UTC)", "(dates en UTC)", "(даты в UTC)", "(fechas en UTC)", "（UTC 日期）", "(UTC में तिथियाँ)"],
"開始日": ["Start date", "Date de début", "Дата начала", "Fecha inicial", "开始日期", "आरंभ तिथि"],
"終了日": ["End date", "Date de fin", "Дата окончания", "Fecha final", "结束日期", "अंतिम तिथि"],
"今後1年": ["Next year", "1 an", "1 год", "1 año", "今后 1 年", "अगला 1 वर्ष"],
"今後10年": ["Next 10 years", "10 ans", "10 лет", "10 años", "今后 10 年", "अगले 10 वर्ष"],
"今後30年": ["Next 30 years", "30 ans", "30 лет", "30 años", "今后 30 年", "अगले 30 वर्ष"],
"今後100年": ["Next 100 years", "100 ans", "100 лет", "100 años", "今后 100 年", "अगले 100 वर्ष"],
"2001〜2100年": ["2001–2100", "2001–2100", "2001–2100", "2001–2100", "2001～2100 年", "2001–2100"],
"詳細設定": ["Advanced settings", "Paramètres avancés", "Дополнительно", "Ajustes avanzados", "详细设置", "उन्नत सेटिंग"],
"（精度・モデル）": ["(accuracy, models)", "(précision, modèles)", "(точность, модели)", "(precisión, modelos)", "（精度·模型）", "(सटीकता, मॉडल)"],
"暦（天体位置の元データ）": ["Ephemeris (source of positions)", "Éphéméride (source des positions)", "Эфемериды (источник положений)", "Efeméride (fuente de las posiciones)", "历表（天体位置数据来源）", "एफ़ेमेरिस (स्थितियों का स्रोत)"],
"自動（IERS＋長期モデル）": ["Automatic (IERS + long-term model)", "Automatique (IERS + modèle à long terme)", "Автоматически (IERS + долгосрочная модель)", "Automático (IERS + modelo a largo plazo)", "自动（IERS＋长期模型）", "स्वचालित (IERS + दीर्घकालिक मॉडल)"],
"手動で指定": ["Manual", "Manuel", "Вручную", "Manual", "手动指定", "मैन्युअल"],
"ΔT の値 (秒)": ["ΔT value (s)", "Valeur de ΔT (s)", "Значение ΔT (с)", "Valor de ΔT (s)", "ΔT 值（秒）", "ΔT का मान (से.)"],
"例 69.2": ["e.g. 69.2", "ex. 69,2", "напр. 69.2", "p. ej. 69.2", "例 69.2", "उदा. 69.2"],
"太陽の半径": ["Radius of the Sun", "Rayon du Soleil", "Радиус Солнца", "Radio del Sol", "太阳半径", "सूर्य की त्रिज्या"],
"IAU 2015 公称値 (695,700 km)": ["IAU 2015 nominal (695,700 km)", "Valeur nominale UAI 2015 (695 700 km)", "Номинал МАС 2015 (695 700 км)", "Valor nominal UAI 2015 (695 700 km)", "IAU 2015 标称值 (695,700 km)", "IAU 2015 नाममात्र (695,700 किमी)"],
"NASA 互換 (959.63″ = 695,992 km)": ["NASA compatible (959.63″ = 695,992 km)", "Compatible NASA (959,63″ = 695 992 km)", "Как у NASA (959,63″ = 695 992 км)", "Compatible con NASA (959,63″ = 695 992 km)", "与 NASA 一致 (959.63″ = 695,992 km)", "NASA अनुरूप (959.63″ = 695,992 किमी)"],
"数値で指定": ["Custom value", "Valeur personnalisée", "Задать число", "Valor personalizado", "指定数值", "कस्टम मान"],
"太陽半径 (km)": ["Sun radius (km)", "Rayon du Soleil (km)", "Радиус Солнца (км)", "Radio del Sol (km)", "太阳半径 (km)", "सूर्य की त्रिज्या (किमी)"],
"月の半径": ["Radius of the Moon", "Rayon de la Lune", "Радиус Луны", "Radio de la Luna", "月球半径", "चंद्रमा की त्रिज्या"],
"NASA方式 (外接 k=0.2725076 / 内接 k=0.272281)": ["NASA method (external k=0.2725076 / internal k=0.272281)", "Méthode NASA (extérieur k=0,2725076 / intérieur k=0,272281)", "Метод NASA (внешний k=0,2725076 / внутренний k=0,272281)", "Método NASA (externo k=0,2725076 / interno k=0,272281)", "NASA 方式（外切 k=0.2725076 / 内切 k=0.272281）", "NASA विधि (बाह्य k=0.2725076 / आंतरिक k=0.272281)"],
"平均半径 1737.4 km": ["Mean radius 1737.4 km", "Rayon moyen 1737,4 km", "Средний радиус 1737,4 км", "Radio medio 1737,4 km", "平均半径 1737.4 km", "माध्य त्रिज्या 1737.4 किमी"],
"外接用 (km)": ["For external contacts (km)", "Contacts extérieurs (km)", "Для внешних контактов (км)", "Contactos externos (km)", "外切用 (km)", "बाह्य स्पर्श के लिए (किमी)"],
"内接用 (km)": ["For internal contacts (km)", "Contacts intérieurs (km)", "Для внутренних контактов (км)", "Contactos internos (km)", "内切用 (km)", "आंतरिक स्पर्श के लिए (किमी)"],
"水星・金星の半径": ["Radii of Mercury and Venus", "Rayons de Mercure et Vénus", "Радиусы Меркурия и Венеры", "Radios de Mercurio y Venus", "水星·金星半径", "बुध और शुक्र की त्रिज्या"],
"IAU 固体表面 (水星 2439.4 / 金星 6051.8 km)": ["IAU solid surface (Mercury 2439.4 / Venus 6051.8 km)", "Surface solide UAI (Mercure 2439,4 / Vénus 6051,8 km)", "Твёрдая поверхность МАС (Меркурий 2439,4 / Венера 6051,8 км)", "Superficie sólida UAI (Mercurio 2439,4 / Venus 6051,8 km)", "IAU 固体表面（水星 2439.4 / 金星 6051.8 km）", "IAU ठोस सतह (बुध 2439.4 / शुक्र 6051.8 किमी)"],
"NASA 互換 (3.36″ / 8.41″ at 1 AU)": ["NASA compatible (3.36″ / 8.41″ at 1 AU)", "Compatible NASA (3,36″ / 8,41″ à 1 UA)", "Как у NASA (3,36″ / 8,41″ на 1 а. е.)", "Compatible con NASA (3,36″ / 8,41″ a 1 UA)", "与 NASA 一致（1 AU 处 3.36″ / 8.41″）", "NASA अनुरूप (1 AU पर 3.36″ / 8.41″)"],
"太陽高度の下限 (°)": ["Minimum Sun altitude (°)", "Hauteur minimale du Soleil (°)", "Минимальная высота Солнца (°)", "Altura mínima del Sol (°)", "太阳高度下限 (°)", "सूर्य की न्यूनतम ऊँचाई (°)"],
"太陽中心がこの高度以上で「見える」": ["“visible” when the Sun’s centre is at least this high", "« visible » si le centre du Soleil est au moins à cette hauteur", "«видно», если центр Солнца не ниже этой высоты", "«visible» si el centro del Sol está al menos a esta altura", "太阳中心在此高度以上视为“可见”", "सूर्य का केंद्र इतनी ऊँचाई या अधिक पर हो तो “दृश्य”"],
"衛星: 地球大気の遮蔽高度 (km)": ["Satellite: height of the blocking atmosphere (km)", "Satellite : hauteur de l’atmosphère occultante (km)", "Спутник: высота затеняющей атмосферы (км)", "Satélite: altura de la atmósfera que oculta (km)", "卫星：地球大气遮挡高度 (km)", "उपग्रह: अवरोधक वायुमंडल की ऊँचाई (किमी)"],
"地球の縁＋この高さまでは太陽が隠れる": ["the Sun is hidden up to this height above the Earth’s limb", "le Soleil est caché jusqu’à cette hauteur au-dessus du bord terrestre", "Солнце скрыто до этой высоты над краем Земли", "el Sol queda oculto hasta esta altura sobre el borde terrestre", "地球边缘＋此高度以内太阳被遮挡", "पृथ्वी के किनारे से इतनी ऊँचाई तक सूर्य छिपा रहता है"],
"大気差（大気による浮き上がり）を太陽高度に反映": ["Apply atmospheric refraction to the Sun’s altitude", "Appliquer la réfraction atmosphérique à la hauteur du Soleil", "Учитывать рефракцию в высоте Солнца", "Aplicar la refracción atmosférica a la altura del Sol", "将大气折射计入太阳高度", "सूर्य की ऊँचाई में वायुमंडलीय अपवर्तन शामिल करें"],
"観測地から見えない現象（夜間・地球の陰）も一覧に含める": ["Also list events not visible from the observer (night, Earth’s shadow)", "Lister aussi les événements invisibles de l’observateur (nuit, ombre de la Terre)", "Показывать и невидимые для наблюдателя явления (ночь, тень Земли)", "Incluir también eventos no visibles para el observador (noche, sombra de la Tierra)", "列表中也包含观测地看不到的天象（夜间、地球阴影）", "प्रेक्षक को न दिखने वाली घटनाएँ भी सूची में दिखाएँ (रात, पृथ्वी की छाया)"],
"計算する": ["Compute", "Calculer", "Рассчитать", "Calcular", "计算", "गणना करें"],
"ようこそ": ["Welcome", "Bienvenue", "Добро пожаловать", "Bienvenido", "欢迎", "स्वागत है"],
"東京で今後30年に見られる日食": ["Solar eclipses visible from Tokyo in the next 30 years", "Éclipses de Soleil visibles à Tokyo dans les 30 prochaines années", "Солнечные затмения в Токио в ближайшие 30 лет", "Eclipses de Sol visibles desde Tokio en los próximos 30 años", "东京今后 30 年可见的日食", "अगले 30 वर्षों में टोक्यो से दिखने वाले सूर्य ग्रहण"],
"2001〜2100年の世界の日食と太陽面通過": ["Solar eclipses and transits worldwide, 2001–2100", "Éclipses de Soleil et transits dans le monde, 2001–2100", "Солнечные затмения и прохождения в мире, 2001–2100", "Eclipses de Sol y tránsitos en el mundo, 2001–2100", "2001～2100 年全球的日食与凌日", "2001–2100 के दुनिया भर के सूर्य ग्रहण और पारगमन"],
"ISS（国際宇宙ステーション）から見える日食": ["Solar eclipses seen from the ISS (International Space Station)", "Éclipses de Soleil vues depuis l’ISS (Station spatiale internationale)", "Солнечные затмения с МКС (Международной космической станции)", "Eclipses de Sol vistos desde la ISS (Estación Espacial Internacional)", "从 ISS（国际空间站）看到的日食", "ISS (अंतर्राष्ट्रीय अंतरिक्ष स्टेशन) से दिखने वाले सूर्य ग्रहण"],
"静止衛星ひまわり9号から見える日食": ["Solar eclipses seen from the geostationary satellite Himawari-9", "Éclipses de Soleil vues depuis le satellite géostationnaire Himawari-9", "Солнечные затмения с геостационарного спутника «Химавари-9»", "Eclipses de Sol vistos desde el satélite geoestacionario Himawari-9", "从静止卫星向日葵 9 号看到的日食", "भूस्थिर उपग्रह हिमावारी-9 से दिखने वाले सूर्य ग्रहण"],
"「ひので」が軌道上で見た 2011年1月4日の金環日食": ["The annular eclipse of 4 January 2011 seen by Hinode in orbit", "L’éclipse annulaire du 4 janvier 2011 vue par Hinode en orbite", "Кольцеобразное затмение 4 января 2011 г., увиденное «Хиноде» с орбиты", "El eclipse anular del 4 de enero de 2011 visto por Hinode en órbita", "“日出”卫星在轨道上看到的 2011 年 1 月 4 日日环食", "कक्षा में हिनोडे द्वारा देखा गया 4 जनवरी 2011 का वलयाकार ग्रहण"],
"打ち上げ前の検討：太陽同期軌道 680 km から 2027年に見える日食": ["Planning before launch: eclipses seen in 2027 from a 680 km sun-synchronous orbit", "Étude avant lancement : éclipses vues en 2027 depuis une orbite héliosynchrone à 680 km", "Анализ до запуска: затмения в 2027 г. с солнечно-синхронной орбиты 680 км", "Estudio antes del lanzamiento: eclipses vistos en 2027 desde una órbita heliosíncrona de 680 km", "发射前的研究：从 680 km 太阳同步轨道看到的 2027 年日食", "प्रक्षेपण से पहले की योजना: 680 किमी सूर्य-समकालिक कक्षा से 2027 में दिखने वाले ग्रहण"],
"高精度": ["High precision", "Haute précision", "Высокая точность", "Alta precisión", "高精度", "उच्च सटीकता"],
": JPL DE440 暦、光行時間・ΔT・地球楕円体（WGS84）を厳密に扱い、接触時刻を 0.1 ms まで追い込みます。": [": the JPL DE440 ephemeris, with light time, ΔT and the Earth ellipsoid (WGS84) handled rigorously; contact times are converged to 0.1 ms.", " : éphéméride JPL DE440, temps de lumière, ΔT et ellipsoïde terrestre (WGS84) traités rigoureusement ; les heures de contact sont convergées à 0,1 ms.", ": эфемериды JPL DE440, строгий учёт времени распространения света, ΔT и эллипсоида Земли (WGS84); моменты контактов уточняются до 0,1 мс.", ": efeméride JPL DE440, con tiempo de luz, ΔT y elipsoide terrestre (WGS84) tratados con rigor; las horas de contacto convergen hasta 0,1 ms.", "：采用 JPL DE440 历表，严格处理光行时、ΔT 和地球椭球（WGS84），接触时刻精确到 0.1 ms。", ": JPL DE440 एफ़ेमेरिस, प्रकाश-समय, ΔT और पृथ्वी दीर्घवृत्तज (WGS84) का कठोर उपयोग; स्पर्श समय 0.1 ms तक निर्धारित।"],
"人工衛星から": ["From satellites", "Depuis des satellites", "Со спутников", "Desde satélites", "从人造卫星看", "उपग्रहों से"],
": TLE（SGP4）、軌道要素、静止衛星、JPL Horizons の探査機軌道、NASA SSCWeb の過去軌道に対応。地球に隠される時間も考慮します。": [": TLE (SGP4), orbital elements, geostationary satellites, spacecraft orbits from JPL Horizons and past orbits from NASA SSCWeb. Times when the Earth hides the Sun are taken into account.", " : TLE (SGP4), éléments orbitaux, satellites géostationnaires, orbites de sondes de JPL Horizons et orbites passées de NASA SSCWeb. Les moments où la Terre cache le Soleil sont pris en compte.", ": TLE (SGP4), элементы орбиты, геостационарные спутники, орбиты аппаратов из JPL Horizons и прошлые орбиты из NASA SSCWeb. Учитывается, когда Солнце закрыто Землёй.", ": TLE (SGP4), elementos orbitales, satélites geoestacionarios, órbitas de sondas de JPL Horizons y órbitas pasadas de NASA SSCWeb. Se tienen en cuenta los momentos en que la Tierra oculta el Sol.", "：支持 TLE（SGP4）、轨道根数、静止卫星、JPL Horizons 的探测器轨道和 NASA SSCWeb 的历史轨道，并考虑被地球遮挡的时间。", ": TLE (SGP4), कक्षीय तत्व, भूस्थिर उपग्रह, JPL Horizons से यान की कक्षाएँ और NASA SSCWeb से पिछली कक्षाएँ। पृथ्वी द्वारा सूर्य छिपाए जाने का समय भी ध्यान में रखा जाता है।"],
"地図": ["Map", "Carte", "Карта", "Mapa", "地图", "मानचित्र"],
": 皆既帯・金環帯の経路と限界線、部分食の食分分布、太陽面通過の可視地域を表示します。": [": paths and limits of totality and annularity, the magnitude of the partial eclipse, and where transits are visible.", " : bandes et limites de totalité et d’annularité, magnitude de l’éclipse partielle et zones de visibilité des transits.", ": полосы и границы полной и кольцеобразной фаз, распределение фазы частного затмения, области видимости прохождений.", ": franjas y límites de totalidad y anularidad, magnitud del eclipse parcial y zonas de visibilidad de los tránsitos.", "：显示全食带·环食带的路径和界线、偏食的食分分布以及凌日的可见地区。", ": पूर्णता और वलय के पथ व सीमाएँ, आंशिक ग्रहण का परिमाण वितरण और पारगमन दिखने के क्षेत्र।"],
"一覧と計算条件を保存します。「保存した結果を開く」で、あとから同じ画面で見返せます": ["Saves the list and the computation settings. Open them later with “Open saved results”", "Enregistre la liste et les conditions de calcul. Rouvrez-les plus tard avec « Ouvrir des résultats enregistrés »", "Сохраняет список и условия расчёта. Позже их можно открыть через «Открыть сохранённые результаты»", "Guarda la lista y las condiciones de cálculo. Vuelva a abrirlas con «Abrir resultados guardados»", "保存列表和计算条件。之后可用“打开保存的结果”在同一界面中查看", "सूची और गणना की शर्तें सहेजता है। बाद में “सहेजे गए परिणाम खोलें” से फिर देखें"],
"結果を保存": ["Save results", "Enregistrer les résultats", "Сохранить результаты", "Guardar resultados", "保存结果", "परिणाम सहेजें"],
"一覧をCSV保存": ["Save list as CSV", "Enregistrer la liste en CSV", "Сохранить список в CSV", "Guardar lista en CSV", "将列表保存为 CSV", "सूची CSV में सहेजें"],
"← 元の現象に戻る": ["← Back to the original event", "← Revenir à l’événement d’origine", "← Вернуться к исходному событию", "← Volver al evento original", "← 返回原天象", "← मूल घटना पर लौटें"],
"閉じる": ["Close", "Fermer", "Закрыть", "Cerrar", "关闭", "बंद करें"],
"概要": ["Overview", "Résumé", "Обзор", "Resumen", "概要", "सारांश"],
"見え方（動画）": ["View (animation)", "Vue (animation)", "Вид (анимация)", "Vista (animación)", "情况（动画）", "दृश्य (एनिमेशन)"],
"グラフ": ["Charts", "Graphiques", "Графики", "Gráficos", "图表", "ग्राफ़"],
"データ": ["Data", "Données", "Данные", "Datos", "数据", "डेटा"],
"向き": ["Orientation", "Orientation", "Ориентация", "Orientación", "方向", "दिशा"],
"方位の目印を表示": ["Show direction marks", "Afficher les repères de direction", "Показывать метки направлений", "Mostrar marcas de dirección", "显示方位标记", "दिशा चिह्न दिखाएँ"],
"再生": ["Play", "Lecture", "Воспроизвести", "Reproducir", "播放", "चलाएँ"],
"再生速度": ["Playback speed", "Vitesse de lecture", "Скорость воспроизведения", "Velocidad de reproducción", "播放速度", "चलाने की गति"],
"グラフ上をクリックすると、その時刻の見え方に移動します。": ["Click a chart to see the view at that time.", "Cliquez sur un graphique pour voir la vue à cet instant.", "Щёлкните график, чтобы перейти к виду в этот момент.", "Haga clic en un gráfico para ver la vista en ese instante.", "点击图表即可跳转到该时刻的情况。", "उस समय का दृश्य देखने के लिए ग्राफ़ पर क्लिक करें।"],
"JSON をダウンロード": ["Download JSON", "Télécharger le JSON", "Скачать JSON", "Descargar JSON", "下载 JSON", "JSON डाउनलोड करें"],
"時系列 CSV をダウンロード": ["Download time series CSV", "Télécharger la série temporelle (CSV)", "Скачать временной ряд (CSV)", "Descargar serie temporal (CSV)", "下载时间序列 CSV", "समय श्रृंखला CSV डाउनलोड करें"],
}/*END-MESSAGES*/;

/* Static parts of index.html with markup (data-i18n-html); Japanese is the page itself. */
const OPEN_BTN = (label) => `<button type="button" class="link" data-open>${label}</button>`;
const HTML_BLOCKS = {
  welcome: {
    en: `Choose <b>① phenomena</b>, <b>② observer</b> and <b>③ period</b> on the left and press “Compute” to list the solar eclipses and transits that can be seen.
      Click a row of the list for detailed results: an animation of the eclipse, charts, a map and more.
      Files saved with “Save results” can be viewed again later in this page with ${OPEN_BTN('Open saved results')}.`,
    fr: `Choisissez <b>① les phénomènes</b>, <b>② l’observateur</b> et <b>③ la période</b> à gauche, puis cliquez sur « Calculer » pour obtenir la liste des éclipses de Soleil et des transits visibles.
      Cliquez sur une ligne de la liste pour voir les résultats détaillés : animation, graphiques, carte, etc.
      Les fichiers créés avec « Enregistrer les résultats » peuvent être revus plus tard dans cette page avec ${OPEN_BTN('Ouvrir des résultats enregistrés')}.`,
    ru: `Выберите слева <b>① явления</b>, <b>② наблюдателя</b> и <b>③ период</b> и нажмите «Рассчитать» — появится список видимых солнечных затмений и прохождений.
      Щёлкните строку списка, чтобы увидеть подробные результаты: анимацию, графики, карту и т. д.
      Файлы, сохранённые кнопкой «Сохранить результаты», можно позже снова открыть на этой странице: ${OPEN_BTN('Открыть сохранённые результаты')}.`,
    es: `Elija <b>① los fenómenos</b>, <b>② el observador</b> y <b>③ el periodo</b> a la izquierda y pulse «Calcular» para ver la lista de eclipses de Sol y tránsitos visibles.
      Haga clic en una fila de la lista para ver los resultados detallados: animación, gráficos, mapa y más.
      Los archivos guardados con «Guardar resultados» se pueden volver a ver más tarde en esta página con ${OPEN_BTN('Abrir resultados guardados')}.`,
    zh: `在左侧选择 <b>①天象</b>、<b>②观测者</b>、<b>③期间</b>，然后点击“计算”，即可列出可见的日食和凌日。
      点击列表中的行，可查看食相动画、图表、地图等详细结果。
      用“保存结果”保存的文件，之后可通过 ${OPEN_BTN('打开保存的结果')} 在同一界面中再次查看。`,
    hi: `बाईं ओर <b>① घटनाएँ</b>, <b>② प्रेक्षक</b> और <b>③ अवधि</b> चुनें और “गणना करें” दबाएँ — दिखने वाले सूर्य ग्रहणों और पारगमनों की सूची आ जाएगी।
      विस्तृत परिणाम (एनिमेशन, ग्राफ़, मानचित्र आदि) के लिए सूची की किसी पंक्ति पर क्लिक करें।
      “परिणाम सहेजें” से सहेजी गई फ़ाइलें बाद में इसी पृष्ठ पर ${OPEN_BTN('सहेजे गए परिणाम खोलें')} से फिर देखी जा सकती हैं।`,
  },
  help: {
    en: `<h2>How to use, and terms</h2>
    <h3>Basic steps</h3>
    <ol>
      <li>Tick the phenomena to compute (solar eclipses, Mercury, Venus).</li>
      <li>Choose the observer. A “Place on Earth” is given by latitude, longitude and elevation; a “Satellite” by NORAD number, TLE, orbital elements, geostationary longitude, JPL Horizons ID or NASA SSCWeb satellite ID. For a satellite not yet launched, enter the planned values under “Orbital elements” and use “Compute for many mean anomalies” to see the range of outcomes wherever the satellite is along its orbit. “Whole Earth” finds the events visible somewhere in the world.</li>
      <li>Set the period and press “Compute”. Click a row of the list to open its details.</li>
    </ol>
    <h3>Saving and reopening results</h3>
    <p>“Save results” saves the list and the computation settings in a JSON file. Choose that file with “Open saved results” at the top: the list is shown as it was saved and the settings on the left are restored.
      Clicking a row computes the details of that event (contacts, animation, charts, map) again with the saved settings. You can also open a single event saved with “Download JSON” in the Data tab of the details, and the output of <code>cli.py --format json</code>.</p>
    <h3>Terms</h3>
    <dl>
      <dt>Magnitude</dt><dd>Fraction of the Sun’s <b>diameter</b> covered by the Moon; 1 or more in a total eclipse (in the whole-Earth list, as at NASA, the ratio of the apparent diameters of the Moon and the Sun for central eclipses).</dd>
      <dt>Obscuration</dt><dd>Fraction of the Sun’s <b>area</b> covered; close to how dark it feels.</dd>
      <dt>1st to 4th contact</dt><dd>Moments when the limb of the Moon (or planet) touches the limb of the Sun: 1st = the eclipse begins, 2nd = totality or annularity (or internal contact) begins, 3rd = it ends, 4th = the eclipse ends.</dd>
      <dt>Position angle P / zenith angle V</dt><dd>Direction of the point of contact seen from the centre of the Sun: P is measured from celestial north towards the east, V from the direction of the zenith.</dd>
      <dt>γ (gamma)</dt><dd>Least distance of the axis of the Moon’s shadow from the centre of the Earth, in Earth equatorial radii. The smaller it is, the nearer the shadow passes to the middle of the Earth.</dd>
      <dt>Saros number</dt><dd>Number of the series of similar eclipses repeating about every 18 years.</dd>
      <dt>ΔT</dt><dd>Difference TT − UT due to the irregular rotation of the Earth. It is more uncertain the further in the future or past, and mainly affects where (at what longitude) an eclipse is seen.</dd>
      <dt>TLE</dt><dd>Standard format of the orbital elements of satellites. Errors grow with the time from the epoch, so predictions are good for a few days around it.</dd>
    </dl>
    <h3>Accuracy</h3>
    <ul>
      <li>Positions come from the JPL DE440 (DE440s) ephemeris, and ΔT from IERS observations. Contact times are found iterating the light time and converged to better than 0.1 ms.</li>
      <li>The relief of the lunar limb (mountains and valleys) is not modelled; the two NASA lunar radii approximate its average effect. Real contact times can differ by ±1–2 s because of the limb profile.</li>
      <li>For satellites, the accuracy is that of the orbit. A TLE gives errors of a few km to hundreds of km as days pass from its epoch.</li>
    </ul>`,
    fr: `<h2>Mode d’emploi et termes</h2>
    <h3>Étapes</h3>
    <ol>
      <li>Cochez les phénomènes à calculer (éclipses de Soleil, Mercure, Vénus).</li>
      <li>Choisissez l’observateur. Un « Lieu sur Terre » se donne par latitude, longitude et altitude ; un « Satellite » par numéro NORAD, TLE, éléments orbitaux, longitude géostationnaire, identifiant JPL Horizons ou identifiant NASA SSCWeb. Pour un satellite pas encore lancé, saisissez les valeurs prévues dans « Éléments orbitaux » et utilisez « Calculer pour plusieurs anomalies moyennes » pour voir l’éventail des résultats quelle que soit sa position sur l’orbite. « Terre entière » recherche les événements visibles quelque part dans le monde.</li>
      <li>Indiquez la période et cliquez sur « Calculer ». Cliquez sur une ligne de la liste pour ouvrir les détails.</li>
    </ol>
    <h3>Enregistrer et rouvrir des résultats</h3>
    <p>« Enregistrer les résultats » sauvegarde la liste et les conditions de calcul dans un fichier JSON. Choisissez ce fichier avec « Ouvrir des résultats enregistrés » en haut : la liste s’affiche telle qu’elle a été enregistrée et les paramètres de gauche sont restaurés.
      Un clic sur une ligne recalcule les détails de l’événement (contacts, animation, graphiques, carte) avec les conditions enregistrées. Vous pouvez aussi ouvrir un événement enregistré avec « Télécharger le JSON » dans l’onglet Données des détails, ainsi que la sortie de <code>cli.py --format json</code>.</p>
    <h3>Termes</h3>
    <dl>
      <dt>Magnitude</dt><dd>Fraction du <b>diamètre</b> du Soleil masquée par la Lune ; 1 ou plus lors d’une éclipse totale (dans la liste « Terre entière », comme à la NASA, rapport des diamètres apparents de la Lune et du Soleil pour les éclipses centrales).</dd>
      <dt>Obscuration</dt><dd>Fraction de la <b>surface</b> du Soleil masquée ; proche de l’assombrissement ressenti.</dd>
      <dt>1er à 4e contact</dt><dd>Instants où le bord de la Lune (ou de la planète) touche le bord du Soleil : 1er = début de l’éclipse, 2e = début de la totalité ou de l’annularité (ou contact intérieur), 3e = sa fin, 4e = fin de l’éclipse.</dd>
      <dt>Angle de position P / angle zénithal V</dt><dd>Direction du point de contact vue du centre du Soleil : P est compté depuis le nord céleste vers l’est, V depuis la direction du zénith.</dd>
      <dt>γ (gamma)</dt><dd>Distance minimale de l’axe de l’ombre de la Lune au centre de la Terre, en rayons équatoriaux terrestres. Plus elle est petite, plus l’ombre passe près du milieu de la Terre.</dd>
      <dt>Numéro de saros</dt><dd>Numéro de la série d’éclipses semblables qui se répètent environ tous les 18 ans.</dd>
      <dt>ΔT</dt><dd>Différence TT − UT due à l’irrégularité de la rotation terrestre. Elle est d’autant plus incertaine qu’on s’éloigne dans le futur ou le passé et influe surtout sur le lieu (la longitude) où l’éclipse est visible.</dd>
      <dt>TLE</dt><dd>Format standard des éléments orbitaux des satellites. L’erreur croît avec l’écart à l’époque : les prévisions sont fiables quelques jours autour de celle-ci.</dd>
    </dl>
    <h3>Précision</h3>
    <ul>
      <li>Positions tirées de l’éphéméride JPL DE440 (DE440s), ΔT issu des observations de l’IERS. Les heures de contact sont obtenues en itérant le temps de lumière et convergées à mieux que 0,1 ms.</li>
      <li>Le relief du bord lunaire (montagnes et vallées) n’est pas modélisé ; les deux rayons lunaires de la NASA en approchent l’effet moyen. Les heures réelles peuvent différer de ±1 à 2 s à cause du profil du bord.</li>
      <li>Pour les satellites, la précision est celle de l’orbite. Un TLE produit des erreurs de quelques km à des centaines de km à mesure que l’on s’éloigne de son époque.</li>
    </ul>`,
    ru: `<h2>Как пользоваться и термины</h2>
    <h3>Порядок работы</h3>
    <ol>
      <li>Отметьте явления для расчёта (солнечные затмения, Меркурий, Венера).</li>
      <li>Выберите наблюдателя. «Место на Земле» задаётся широтой, долготой и высотой; «Спутник» — номером NORAD, TLE, элементами орбиты, долготой геостационарной точки, идентификатором JPL Horizons или NASA SSCWeb. Для ещё не запущенного спутника введите проектные значения в «Элементы орбиты» и включите «Расчёт для разных средних аномалий» — так виден диапазон результатов при любом положении спутника на орбите. «Вся Земля» ищет явления, видимые где-либо в мире.</li>
      <li>Задайте период и нажмите «Рассчитать». Щёлкните строку списка, чтобы открыть подробности.</li>
    </ol>
    <h3>Сохранение и повторный просмотр</h3>
    <p>«Сохранить результаты» записывает список и условия расчёта в файл JSON. Выберите этот файл кнопкой «Открыть сохранённые результаты» вверху — список появится в сохранённом виде, а параметры слева вернутся к сохранённым условиям.
      При щелчке по строке подробности события (контакты, анимация, графики, карта) пересчитываются по сохранённым условиям. Можно также открыть отдельное событие, сохранённое через «Скачать JSON» на вкладке «Данные», и вывод <code>cli.py --format json</code>.</p>
    <h3>Термины</h3>
    <dl>
      <dt>Фаза</dt><dd>Доля <b>диаметра</b> Солнца, закрытая Луной; при полном затмении 1 и более (в списке для всей Земли, как у NASA, для центральных затмений — отношение видимых диаметров Луны и Солнца).</dd>
      <dt>Доля закрытой площади</dt><dd>Доля <b>площади</b> Солнца, закрытая Луной; близка к ощущаемому потемнению.</dd>
      <dt>1-й – 4-й контакты</dt><dd>Моменты касания края Луны (или планеты) с краем Солнца: 1-й — начало затмения, 2-й — начало полной или кольцеобразной фазы (или внутренний контакт), 3-й — её конец, 4-й — конец затмения.</dd>
      <dt>Позиционный угол P / зенитный угол V</dt><dd>Направление на точку контакта из центра Солнца: P отсчитывается от северного полюса мира к востоку, V — от направления на зенит.</dd>
      <dt>γ (гамма)</dt><dd>Наименьшее расстояние оси лунной тени от центра Земли в экваториальных радиусах Земли. Чем оно меньше, тем ближе к середине Земли проходит тень.</dd>
      <dt>Номер сароса</dt><dd>Номер серии похожих затмений, повторяющихся примерно через 18 лет.</dd>
      <dt>ΔT</dt><dd>Разность TT − UT, вызванная неравномерностью вращения Земли. Чем дальше в будущее или прошлое, тем она менее определённа; в основном влияет на то, где (на какой долготе) видно затмение.</dd>
      <dt>TLE</dt><dd>Стандартный формат элементов орбиты спутников. Ошибка растёт с удалением от эпохи, поэтому прогноз надёжен лишь несколько суток около неё.</dd>
    </dl>
    <h3>О точности</h3>
    <ul>
      <li>Положения берутся из эфемерид JPL DE440 (DE440s), ΔT — по наблюдениям IERS. Моменты контактов находятся с итерацией времени распространения света и уточняются лучше чем до 0,1 мс.</li>
      <li>Рельеф края Луны (горы и долины) не учитывается; два радиуса Луны по методу NASA приближают его средний эффект. Реальные моменты контактов могут отличаться на ±1–2 с из-за профиля края.</li>
      <li>Для спутников точность определяется точностью орбиты. TLE с удалением от эпохи даёт ошибки от нескольких до сотен километров.</li>
    </ul>`,
    es: `<h2>Uso y términos</h2>
    <h3>Pasos básicos</h3>
    <ol>
      <li>Marque los fenómenos que desea calcular (eclipses de Sol, Mercurio, Venus).</li>
      <li>Elija el observador. Un «Lugar en la Tierra» se indica con latitud, longitud y altitud; un «Satélite», con número NORAD, TLE, elementos orbitales, longitud geoestacionaria, ID de JPL Horizons o ID de NASA SSCWeb. Para un satélite aún no lanzado, introduzca los valores previstos en «Elementos orbitales» y use «Calcular para varias anomalías medias» para ver el rango de resultados esté donde esté el satélite en su órbita. «Toda la Tierra» busca los eventos visibles en algún lugar del mundo.</li>
      <li>Indique el periodo y pulse «Calcular». Haga clic en una fila de la lista para abrir los detalles.</li>
    </ol>
    <h3>Guardar y volver a abrir resultados</h3>
    <p>«Guardar resultados» guarda la lista y las condiciones de cálculo en un archivo JSON. Elija ese archivo con «Abrir resultados guardados» arriba: la lista se muestra tal como se guardó y los ajustes de la izquierda vuelven a las condiciones guardadas.
      Al hacer clic en una fila se recalculan los detalles del evento (contactos, animación, gráficos, mapa) con las condiciones guardadas. También puede abrir un evento guardado con «Descargar JSON» en la pestaña Datos de los detalles, y la salida de <code>cli.py --format json</code>.</p>
    <h3>Términos</h3>
    <dl>
      <dt>Magnitud</dt><dd>Fracción del <b>diámetro</b> del Sol cubierta por la Luna; 1 o más en un eclipse total (en la lista de toda la Tierra, como en la NASA, la razón de los diámetros aparentes de la Luna y el Sol en los eclipses centrales).</dd>
      <dt>Oscurecimiento</dt><dd>Fracción del <b>área</b> del Sol cubierta; cercana a la oscuridad que se percibe.</dd>
      <dt>1.er a 4.º contacto</dt><dd>Instantes en que el borde de la Luna (o del planeta) toca el borde del Sol: 1.º = empieza el eclipse, 2.º = empieza la totalidad o la anularidad (o contacto interno), 3.º = termina, 4.º = termina el eclipse.</dd>
      <dt>Ángulo de posición P / ángulo cenital V</dt><dd>Dirección del punto de contacto vista desde el centro del Sol: P se mide desde el norte celeste hacia el este, V desde la dirección del cenit.</dd>
      <dt>γ (gamma)</dt><dd>Distancia mínima del eje de la sombra lunar al centro de la Tierra, en radios ecuatoriales terrestres. Cuanto menor es, más cerca del centro de la Tierra pasa la sombra.</dd>
      <dt>Número de saros</dt><dd>Número de la serie de eclipses parecidos que se repiten cada unos 18 años.</dd>
      <dt>ΔT</dt><dd>Diferencia TT − UT debida a la rotación irregular de la Tierra. Es más incierta cuanto más lejos en el futuro o el pasado, y afecta sobre todo a dónde (en qué longitud) se ve el eclipse.</dd>
      <dt>TLE</dt><dd>Formato estándar de los elementos orbitales de los satélites. El error crece al alejarse de la época, así que las predicciones valen unos pocos días alrededor de ella.</dd>
    </dl>
    <h3>Precisión</h3>
    <ul>
      <li>Las posiciones proceden de la efeméride JPL DE440 (DE440s) y ΔT de las observaciones del IERS. Las horas de contacto se obtienen iterando el tiempo de luz y convergen a menos de 0,1 ms.</li>
      <li>No se modela el relieve del borde lunar (montañas y valles); los dos radios lunares de la NASA aproximan su efecto medio. Las horas reales pueden diferir ±1–2 s por el perfil del borde.</li>
      <li>En los satélites, la precisión es la de la órbita. Un TLE produce errores de unos pocos km a cientos de km a medida que pasan los días desde su época.</li>
    </ul>`,
    zh: `<h2>用法与术语</h2>
    <h3>基本流程</h3>
    <ol>
      <li>勾选要计算的天象（日食、水星、金星）。</li>
      <li>选择观测者。“地面地点”用纬度、经度、海拔指定，“人造卫星”用 NORAD 编号、TLE、轨道根数、静止卫星经度、JPL Horizons ID 或 NASA SSCWeb 卫星 ID 指定。尚未发射的卫星可在“轨道根数”中输入计划值，并使用“改变平近点角批量计算”，了解卫星位于轨道上任意位置时可能出现的结果范围。“全球”会查找世界上某处可见的天象。</li>
      <li>指定期间后点击“计算”。点击列表中的行可打开详细信息。</li>
    </ol>
    <h3>保存与重新查看结果</h3>
    <p>列表中的“保存结果”会将列表和计算条件保存为 JSON 文件。用上方的“打开保存的结果”选择该文件，列表会按保存时的样子显示，左侧设置也会恢复为保存的条件。
      点击列表中的行，会按保存的计算条件重新计算并显示该天象的详细信息（接触时刻、动画、图表、地图）。也可以打开在详细信息“数据”标签页中用“下载 JSON”保存的单个天象，以及 <code>cli.py --format json</code> 的输出。</p>
    <h3>术语</h3>
    <dl>
      <dt>食分</dt><dd>太阳<b>直径</b>被月球遮住的比例。日全食时大于等于 1（全球列表中与 NASA 相同，中心食为月球与太阳视直径之比）。</dd>
      <dt>食面积比</dt><dd>太阳<b>面积</b>被遮住的比例，接近体感上的昏暗程度。</dd>
      <dt>第一至第四接触</dt><dd>月球（或行星）边缘与太阳边缘相切的时刻。第一接触＝初亏，第二接触＝全食·环食（或内切）开始，第三接触＝其结束，第四接触＝复圆。</dd>
      <dt>位置角 P / 天顶角 V</dt><dd>从日面中心看接触点的方向。P 从天北极方向向东量，V 从天顶方向量。</dd>
      <dt>γ（伽马）</dt><dd>月影轴与地心的最近距离（以地球赤道半径为单位）。越小，影子越靠近地球中央通过。</dd>
      <dt>沙罗序列号</dt><dd>约 18 年周期重复出现的相似日食序列的编号。</dd>
      <dt>ΔT</dt><dd>反映地球自转不规则性的 TT 与 UT 之差。越远的未来或过去越不确定，主要影响食的可见地点（经度）。</dd>
      <dt>TLE</dt><dd>人造卫星轨道根数的标准格式。离历元越远误差越大，卫星预报以历元前后几天为宜。</dd>
    </dl>
    <h3>关于精度</h3>
    <ul>
      <li>天体位置采用 JPL DE440（DE440s）历表，时间系统采用基于 IERS 观测值的 ΔT。接触时刻经光行时迭代计算，收敛到 0.1 ms 以下。</li>
      <li>未考虑月面边缘的起伏（山谷）。用 NASA 方式的两种月球半径近似其平均效应。实际接触时刻可能因月缘地形相差 ±1～2 秒。</li>
      <li>人造卫星的精度取决于轨道信息的精度。TLE 离历元的天数越多，误差可达数千米到数百千米。</li>
    </ul>`,
    hi: `<h2>उपयोग और शब्दावली</h2>
    <h3>मूल चरण</h3>
    <ol>
      <li>जिन घटनाओं की गणना करनी है (सूर्य ग्रहण, बुध, शुक्र), उन्हें चुनें।</li>
      <li>प्रेक्षक चुनें। “धरती पर स्थान” अक्षांश, देशांतर और ऊँचाई से; “उपग्रह” NORAD संख्या, TLE, कक्षीय तत्व, भूस्थिर देशांतर, JPL Horizons ID या NASA SSCWeb उपग्रह ID से दिया जाता है। जो उपग्रह अभी प्रक्षेपित नहीं हुआ, उसके लिए “कक्षीय तत्व” में योजनाबद्ध मान डालें और “कई माध्य असंगतियों के लिए गणना” चुनें — इससे पता चलता है कि उपग्रह कक्षा में कहीं भी हो, परिणाम किस सीमा में होंगे। “पूरी पृथ्वी” दुनिया में कहीं भी दिखने वाली घटनाएँ खोजता है।</li>
      <li>अवधि दें और “गणना करें” दबाएँ। विवरण खोलने के लिए सूची की किसी पंक्ति पर क्लिक करें।</li>
    </ol>
    <h3>परिणाम सहेजना और फिर से खोलना</h3>
    <p>“परिणाम सहेजें” सूची और गणना की शर्तों को एक JSON फ़ाइल में सहेजता है। ऊपर “सहेजे गए परिणाम खोलें” से वह फ़ाइल चुनें — सूची वैसी ही दिखेगी जैसी सहेजी गई थी और बाईं ओर की सेटिंग भी लौट आएँगी।
      किसी पंक्ति पर क्लिक करने से सहेजी गई शर्तों से उस घटना का विवरण (स्पर्श, एनिमेशन, ग्राफ़, मानचित्र) फिर से गणना होकर दिखता है। विवरण के “डेटा” टैब में “JSON डाउनलोड करें” से सहेजी गई एक घटना और <code>cli.py --format json</code> का आउटपुट भी खोला जा सकता है।</p>
    <h3>शब्दावली</h3>
    <dl>
      <dt>परिमाण</dt><dd>सूर्य के <b>व्यास</b> का वह भाग जो चंद्रमा से ढका है; पूर्ण ग्रहण में 1 या अधिक (पूरी पृथ्वी की सूची में, NASA की तरह, केंद्रीय ग्रहणों के लिए चंद्रमा और सूर्य के आभासी व्यासों का अनुपात)।</dd>
      <dt>आच्छादन</dt><dd>सूर्य के <b>क्षेत्रफल</b> का ढका भाग; महसूस होने वाले अँधेरे के करीब।</dd>
      <dt>प्रथम से चतुर्थ स्पर्श</dt><dd>वे क्षण जब चंद्रमा (या ग्रह) का किनारा सूर्य के किनारे को छूता है: प्रथम = ग्रहण आरंभ, द्वितीय = पूर्णता या वलय (या आंतरिक स्पर्श) आरंभ, तृतीय = उसका अंत, चतुर्थ = ग्रहण समाप्त।</dd>
      <dt>स्थिति कोण P / शिरोबिंदु कोण V</dt><dd>सूर्य के केंद्र से स्पर्श बिंदु की दिशा: P खगोलीय उत्तर से पूर्व की ओर, V शिरोबिंदु की दिशा से मापा जाता है।</dd>
      <dt>γ (गामा)</dt><dd>चंद्रमा की छाया के अक्ष की पृथ्वी के केंद्र से न्यूनतम दूरी (पृथ्वी की विषुवतीय त्रिज्या की इकाई में)। यह जितनी कम हो, छाया पृथ्वी के बीच के उतने निकट से गुज़रती है।</dd>
      <dt>सारोस संख्या</dt><dd>लगभग हर 18 वर्ष में दोहराए जाने वाले समान ग्रहणों की श्रृंखला की संख्या।</dd>
      <dt>ΔT</dt><dd>पृथ्वी के अनियमित घूर्णन के कारण TT − UT का अंतर। भविष्य या अतीत में जितना दूर, उतना अनिश्चित; मुख्यतः यह प्रभावित करता है कि ग्रहण कहाँ (किस देशांतर पर) दिखेगा।</dd>
      <dt>TLE</dt><dd>उपग्रहों के कक्षीय तत्वों का मानक प्रारूप। युग से दूरी के साथ त्रुटि बढ़ती है, इसलिए पूर्वानुमान युग के आसपास कुछ दिनों तक ही भरोसेमंद है।</dd>
    </dl>
    <h3>सटीकता</h3>
    <ul>
      <li>स्थितियाँ JPL DE440 (DE440s) एफ़ेमेरिस से और ΔT IERS प्रेक्षणों से। स्पर्श समय प्रकाश-समय की पुनरावृत्ति से निकाले जाते हैं और 0.1 ms से बेहतर तक अभिसरित होते हैं।</li>
      <li>चंद्रमा के किनारे की ऊबड़-खाबड़ सतह (पहाड़ और घाटियाँ) शामिल नहीं है; NASA की दो चंद्र त्रिज्याएँ उसके औसत प्रभाव का अनुमान देती हैं। किनारे की बनावट के कारण वास्तविक स्पर्श समय ±1–2 से. अलग हो सकते हैं।</li>
      <li>उपग्रहों के लिए सटीकता कक्षा की सटीकता पर निर्भर है। युग से दिन बीतने के साथ TLE में कुछ किमी से सैकड़ों किमी तक की त्रुटि आ जाती है।</li>
    </ul>`,
  },
};
