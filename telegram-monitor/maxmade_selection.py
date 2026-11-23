"""MAXMADE adapter: explicit requested result and work direction, not keywords."""
import re
from message_classification import positive
from selection_facts import facts,quote,REQUEST
from intent_frames import extract

OUTSIDE_BRIEF = (
    r'^\s*(?:#\w+\s+)*(?:(?:нуж\w*|ищ\w*|требу\w*)\s+(?:уникальн\w*\s+)?)?'
    r'(?:(?:интернет[- ]?)?маркетолог\w*|копирайтер\w*|3d[- ]модел\w*|'
    r'(?:разверн\w*|настро\w*|установ\w*)\s+(?:почтов\w*|постов\w*)\s+сервер\w*|'
    r'(?:обработ\w*|отретуш\w*|ретуш\w*)\s+(?:\d+\s+)?(?:фото(?:графи\w*)?|снимк\w*)|'
    r'зарегистрир\w*\s+на\s+сайт\w*|'
    r'площадк\w*\s+для\s+размещени\w*\s+помещени\w*|'
    r'настройк\w*\s+(?:яндекс\.?\s*)?метрик\w*)\b'
    r'|\b(?:ищ\w*|нуж\w*|требу\w*)\s+(?:интернет[- ]?)?(?:маркетолог\w*|копирайтер\w*|таргетолог\w*|seo[- ]специалист\w*)\b'
    r'|\b(?:(?:ищ\w*|нуж\w*|требу\w*)\s+специалист\w*\s+(?:для|по)\s+|комплексн\w*\s+)'
    r'продвижени\w*\s+(?:[а-я-]+\s+){0,3}сайт\w*'
    r'|\b(?:стратеги\w*|услуг\w*|задач\w*)\s+seo[- ]продвижени\w*'
)
PROMOTION_TASK = (
    r'\b(?:раскрутить|продвинуть)\s+(?:(?:наш\w*|нов\w*|свой\w*|этот|существующ\w*)\s+)?'
    r'(?:сайт\w*|лендинг\w*|магазин\w*|ресурс\w*|каталог\w*)\b'
    r'|\b(?:сайт\w*|лендинг\w*|магазин\w*|ресурс\w*|каталог\w*)\b[^.!?;]{0,70}'
    r'\b(?:требует\s+раскрутки|раскрутить|продвинуть)\b'
    r'|\b(?:вывести|поднять|попасть|выйти|зайти)\b.{0,65}\bв\s+топ\b'
    r'|\b(?:вывести|поднять|повысить|улучшить|занять)\s+(?:первые\s+)?позици\w*\b.{0,55}\b(?:поиск\w*|выдач\w*|сайт\w*)\b'
    r'|\b(?:закупк\w*|закупить|купить|размещение|разместить)\s+(?:внешн\w*|обратн\w*|seo[- ]?)?\s*ссылок\b'
    r'|\b(?:крауд[- ]?ссылк\w*|крауд[- ]?маркетинг\w*|прогоны\s+по\s+каталог\w*)\b'
    r'|^\s*(?:(?:нуж\w*|требу\w*)\s+)?(?:seo|сео)[\s-]+(?:продвижени\w*|оптимизаци\w*|аудит\w*)\b'
    r'|\b(?:настроить|настройк\w*|запустить|запуск|вести|ведени\w*)\s+'
    r'(?:(?:контекстн\w*|таргетированн\w*|медийн\w*)\s+реклам\w*|яндекс[.\s]+директ\w*|google\s+ads)\b'
    r'|\bпродвижени\w*\s+(?:сайт\w*\s+в\s+топ|(?:двух\s+)?telegram[- ]канал\w*)\b'
    r'|\b(?:собрать|сбор)\s+(?:полн\w*\s+)?семантическ\w*\s+ядр\w*\b'
    r'|\b(?:поставщик\w*\s+трафик\w*|накрутк\w*\s+статистик\w*|привлечь\s+продажи\s+на\s+сайт\w*)\b'
    r'|^\s*(?:(?:требуетс\w*|ищ\w*)\s+(?:опытн\w*\s+)?)?'
    r'(?:директолог\w*|контент[- ]маркетолог\w*|работа\s+с\s+репутаци\w*|аналитическ\w*\s+срез\w*\s+сайт\w*)\b'
    r'|\bспециалист\w*\s+(?:по\s+)?(?:я(?:ндекс)?\.\s*директ\w*|яндекс\s+директ\w*)\b'
    r'|\bнужно\s+продвигать\b[^.!?;]{0,50}\b(?:seo|сео|гео)\b'
    r'|\b(?:сайт\w*|лендинг\w*|магазин\w*)\s+(?:(?:стал|стали|был|были)\s+)?'
    r'выше\s+в\s+(?:поисков\w*\s+)?(?:выдач\w*|поиск\w*)\b'
    r'|\b(?:вести|запустить|заказать|начать)\s+(?:(?:поисков\w*|seo|сео)[- ]+)?'
    r'продвижени\w*\s+(?:сайт\w*|лендинг\w*|магазин\w*)\b'
    r'|\b(?:по|с)\s+(?:(?:поисков\w*|seo|сео)[- ]+)?продвижени\w*\s+'
    r'(?:(?:наш\w*|сво\w*|этого|действующ\w*)\s+)?(?:сайт\w*|лендинг\w*|магазин\w*)\b'
    r'|\b(?:сайт\w*|лендинг\w*|магазин\w*)\s+(?:и|плюс)\s+(?:его\s+)?'
    r'(?:поисков\w*\s+)?продвижени\w*\b'
    r'|\b(?:специалист\w*|исполнител\w*|работ\w*)\s+по\s+(?:поисков\w*|seo|сео)[- ]+'
    r'оптимизаци\w*\s+(?:сайт\w*|лендинг\w*|магазин\w*)\b'
    r'|\bссылочн\w*\s+продвижени\w*\s+(?:сайт\w*|лендинг\w*|магазин\w*)\b'
    r'|\bпродвижени\w*\s+(?:сайт\w*|лендинг\w*|магазин\w*)\s+в\s+(?:geo|гео|ии[- ]поиск\w*|нейросет\w*)\b'
    r'|\b(?:вывести|поднять)\b[^.!?;]{0,70}\bна\s+перв\w*\s+страниц\w*\s+(?:google|гугл\w*|яндекс\w*)\b'
    r'|\bв\s+перв\w*\s+(?:десятк\w*|пятерк\w*|тройк\w*)\s+(?:google|гугл\w*|яндекс\w*|поисков\w*\s+выдач\w*)\b'
    r'|\b(?:рост\w*|увеличени\w*|увеличить)\s+(?:органическ\w*\s+)?(?:посещаемост\w*|трафик\w*)'
    r'(?:\s+(?:сайт\w*|лендинг\w*))?\s+из\s+(?:органическ\w*\s+)?поиск\w*\b'
)
OTHER_DELIVERABLE = (
    r'^\s*(?:сделать|нарисовать|подготовить)\s+(?:\d+\s+)?(?:картинк\w*|иллюстраци\w*)\b'
    r'|\b(?:векторн\w*\s+отрисовк\w*|отрисовать\s+в\s+векторе)\b'
    r'|\b(?:создать|поднять|арендовать)\s+(?:нов\w*\s+)?(?:vps|vds|выделенн\w*\s+сервер\w*)\b'
    r'|\b(?:создать|поднять)\s+(?:нов\w*\s+)?сервер\w*\b.{0,50}\bразвернуть\b'
    r'|\b(?:восстановить|развернуть)\s+(?:(?:наш\w*|свой\w*)\s+)?сайт\w*\s+из\s+(?:бэкап\w*|резервн\w*\s+копи\w*)\b'
)
SALES_WORK = (
    r'\b(?:ассистент\w*|менеджер\w*)\s+по\s+(?:рассылк\w*|продаж\w*|лидогенерац\w*|маркетплейс\w*)\b'
    r'|\b(?:найти|поиск\w*|привести|познакомить\s+с)\s+(?:b2b[- ]|потенциальн\w*\s+|тепл\w*\s+)?'
    r'(?:клиент\w*|лид\w*|заказчик\w*)\b'
    r'|\bпредложить\s+разработк\w*\s+сайт\w*\s+по\s+наш\w*\s+баз\w*\b'
)
LEAD_SEARCH = (
    r'\b(?:искать|найти|привести)\s+'
    r'(?:(?:нов\w*|тепл\w*|потенциальн\w*)\s+)?лид(?:а|ы|ов)\b'
)
CONTENT_WORK = (
    r'^\s*(?:(?:нуж\w*|требу\w*)\s+(?:опытн\w*\s+)?)?'
    r'(?:переводчик\w*|сценарист\w*|reels[- ]мейкер\w*|репетитор\w*|монтаж\w*\s+(?:\d+[-\w]*\s+)?видео\w*|'
    r'монтаж\s+строго|ии[- ]генераци\w*\s+изображени\w*|3d[- ]персонаж\w*|оформлени\w*\s+постов|'
    r'договор\s+на|оформить\s+список\s+использованн\w*\s+источник\w*)\b'
    r'|\b(?:смонтировать|склеить)\s+(?:видеоурок\w*|видео\w*|ролик\w*|урок\w*)\b'
    r'|\b(?:создать|сделать|записать)\s+(?:(?:коротк\w*|игров\w*)\s+){0,2}видео\w*\s+(?:к\s+треку|для\s+игр\w*|\(тест)'
    r'|\b(?:сделать|нарисовать)\s+иконк\w*\b[^.!?;]{0,70}\bдля\s+(?:категори\w*|сайт\w*)\b'
    r'|\b(?:сделать|нарисовать)\s+(?:макет\w*|иконк\w*)\b[^.!?;]{0,60}\b(?:под\s+печать|печатн\w*\s+объявлени\w*)\b'
    r'|\b(?:дизайн|макет\w*|верстк\w*)\s+печатн\w*\s+объявлени\w*\b'
    r'|\bслайд\w*\s+для\s+карточк\w*\s+товар\w*\b'
    r'|\b(?:дизайн|разработк\w*|создани\w*)\s+(?:упаков\w*|этикет\w*|афиш\w*)\b'
    r'|\bперевести\s+(?:текст\w*|описани\w*|интерфейс\w*|материал\w*)\s+(?:с\s+\w+\s+)?на\s+\w+\s+язык\b'
    r'|\b(?:подготовить|составить)\s+(?:шаблон\s+)?(?:договор\w*|документ\w*\s+по\s+152[- ]?фз)\b'
    r'|\bподготовить\s+все\s+по\s+152[- ]?фз\b'
)
OPERATIONS_WORK = (
    r'^\s*(?:(?:нуж\w*|требу\w*)\s+)?(?:тестировщик\w*\s+сайт\w*|тестировани\w*\s+сайт\w*|'
    r'разместить\s+отзыв\w*|база\s+сайт\w*|выгрузка\s+открыт\w*\s+контакт\w*|'
    r'сбор\s+открыт\w*\s+данн\w*\s+и\s+контакт\w*)\b'
    r'|\b(?:обеспечить|накрутить)\s+регистраци\w*\s+на\s+сайт\w*\b'
    r'|\b(?:загрузить|заполнить|добавить)\s+\d+\s+(?:готов\w*\s+)?карточ\w*\b[^.!?;]{0,75}\b(?:админк\w*|по\s+шаблон\w*)\b'
    r'|\b(?:загрузить|перенести|внести)\s+(?:вручную\s+)?(?:готов\w*\s+)?'
    r'(?:фото(?:графи\w*)?|описани\w*)\b[^.!?;]{0,65}\bтовар\w*\b[^.!?;]{0,45}\b(?:вручную|по\s+шаблон\w*)\b'
    r'|\b(?:собрать|сбор)\s+(?:свеж\w*\s+)?баз\w*\s+(?:нов\w*\s+)?(?:сайт\w*|контакт\w*)\b'
    r'|\b(?:перенести|перенос)\s+сайт\w*\s+на\s+(?:друг\w*\s+)?хостинг\w*\b'
    r'|\b(?:загрузить|разместить|развернуть)\s+готов\w*\s+сайт\w*\s+из\s+архив\w*\b'
    r'|\b(?:настроить|настройк\w*)\b[^.!?;]{0,30}\bs3\s+хранилищ\w*\b'
    r'|\bобновлени\w*\s+модул\w*\s+джумла\w*\b'
    r'|\b(?:восстановить|востоновить)\s+работу\s+сайт\w*\b[^.!?;]{0,140}\b(?:падени\w*|сбо\w*)\s+хостинг\w*\b'
    r'|\bанализ\w*\s+конкурент\w*\s+сайт\w*\b'
    r'|\b(?:вести|ведени\w*|администрировани\w*)\s+(?:(?:личн\w*|онлайн)\s+)?'
    r'(?:кабинет\w*|магазин\w*)\s+(?:(?:на|в)\s+)?(?:ozon|озон\w*|wildberries|wb|яндекс[.\s]+маркет\w*|маркетплейс\w*)\b'
)
SOFTWARE_WORK = (
    r'\b(?:создать|разработать|создани\w*|разработк\w*)\s+(?:ии[- ]агент\w*|программ\w*[- ]парсер\w*)\b'
    r'|\b(?:сделать|создать|разработать|создани\w*|разработк\w*)\s+(?:telegram|телеграм)[- ]?бот\w*\b'
    r'|\b(?:сделать|создать|разработать|создани\w*|разработк\w*|нужн\w*)\s+'
    r'(?:(?:для\s+него|на базе сайта)\s+)?(?:pwa|progressive\s+web\s+app)(?:[- ]приложени\w*)?\b'
    r'|\b(?:ищ\w*|нуж\w*|требу\w*)\s+(?:pwa[- ]разработчик\w*|разработчик\w*\s+pwa)\b'
    r'|\b(?:установ(?:ить|к\w*)|разверн\w*|развертывани\w*|настроить|настройк\w*)\s+'
    r'(?:(?:локальн\w*|готов\w*)\s+)?(?:open[ -]?webui|ollama)\b'
    r'|\b(?:сделать|создать|разработать)\s+сервис\w*\s+с\s+(?:вирт\.?|виртуальн\w*)\s+карт\w*\b'
    r'|\b(?:потом|затем)\s+(?:сделать|создать|разработать)\s+(?:мобильн\w*\s+)?приложени\w*\b'
    r'|^\s*ландшафтн\w*\s+дизайн\w*\s+через\s+ии\b'
    r'|^\s*мобильн\w*\s+приложени\w*\b.{0,220}\b(?:android\b|ios\b|flutter\b|создать\s+приложени\w*)'
)
DESIGN_VIDEO_BUNDLE = (
    r'\b(?:дизайнер\w*(?:\s+и\s+|\s*[,/+\-–—]\s*)(?:видео[- ]?)?монтажер\w*'
    r'|(?:видео[- ]?)?монтажер\w*(?:\s+и\s+|\s*[,/+\-–—]\s*)дизайнер\w*)\b'
)
VIDEO_DELIVERABLE = r'\b(?:смонтир\w*|монтаж\w*)\b[^.!?;]{0,75}\b(?:reels|рилс\w*|ролик\w*|видео\w*)\b'
SYSTEM_REQUEST = (
    r'\b(?:нуж\w*|надо|сделать|создать|разработать|настроить|внедрить|создани\w*|разработк\w*|настройк\w*)\s+'
    r'(?:(?:нов\w*|внутренн\w*|прост\w*)\s+)?'
    r'(?:веб[- ]систем\w*|систем\w*\s+(?:автоматизаци\w*|(?:прием\w*|обработк\w*|учет\w*)\s+заяв(?:ок|к\w*))'
    r'|автоматизаци\w*|онлайн[- ]бриф\w*|crm)\b'
)
SERVICE_AD = (
    r'\b(?:ищу\s+удаленн\w*\s+работ\w*|если\s+кому[- ]то\s+нуж\w*|'
    r'помогу\s+воплотить\s+ваш\w*\s+иде\w*|хотим\s+создать\s+для\s+вас\s+(?:визитк\w*|сайт\w*|лендинг\w*))\b'
    r'|\bнужн\w*\s+консультаци\w*\s*[-—:]\s*обращайтесь\b'
)
INFORMATION_POST = (
    r'\b(?:вы\s+можете\s+использовать\s+любого\s+ии|обратитесь\s+в\s+службу\s+поддержки|'
    r'попробуйте\s+сыграть\s+и\s+поделитесь|на\s+авито\s+распродажа\s+оборудовани\w*|'
    r'могу\s+поделиться\s+бесплатной\s+платформой)\b'
    r'|\bкак\s+создать\s+онлайн[- ]курс\w*[^?]{0,120}\?\s+на\s+тильд\w*\s+можно\b'
    r'|\bс\s+помощью\s+вайб[- ]блока\s+можно\s+добавить\s+на\s+сайт\w*\b'
)
ROLE_WITHOUT_SCOPE = (
    r'\b(?:в\s+(?:(?:креативн\w*|дизайн[- ])\s*)?студи\w*\s+требу\w*|агентство\s+ищ\w*|ищу\s+топов\w*)\s+'
    r'(?:веб[- ]?)?дизайнер\w*\b'
    r'|\bтребуется\s+разработчик\s+для\s+создания\s+сайтов\b'
    r'|\bищ\w*\s+(?:веб[- ]?)?дизайнер\w*\s+для\s+развити\w*\s+digital[- ]направлени\w*\b'
)
SCOPE_REVIEW = (
    r'\b(?:для\s+тестировани\w*\s+(?:ai|ии)[- ]?сервис\w*|интерфейс\w*\s+(?:в\s+игре|для\s+игр\w*|браузерн\w*\s+игр\w*)|'
    r'(?:сайт\w*|интернет[- ]магазин\w*)\s+с\s+систем\w*\s+учет\w*|'
    r'какие\s+задачи\s+прилетают|что\s+прилетает\s+чаще\s+всего)\b'
    r'|\bскину\s+где\s+заказ\b'
    r'|\bразместить\s+(?:его|сайт)\s+на\s+(?:площадк\w*\s+)?авито\b'
    r'|\bкарт\w*\s+с\s+расчетом\s+маршрут\w*|\bформул\w*\s+считают\b'
    r'|\bподключени\w*\s+онлайн[- ]оплат\w*'
)
PROJECT_RESULT = (
    r'(?:(?:полноценн\w*|адаптивн\w*|корпоративн\w*|одностраничн\w*|двуязычн\w*|'
    r'нов\w*|прост\w*)\s+){0,2}(?:веб[- ])?'
    r'(?:сайт\w*|лендинг?\w*|интерфейс\w*|презентаци\w*)\b'
)
PROJECT_TITLE = (
    r'\A\s*(?:(?:необходимо|нужно|надо)\s+)?'
    r'(?:создать|сделать|разработать|сверстать|доработать|переделать)\s+'
    r'(?:дизайн\s+)?' + PROJECT_RESULT
)
STRUCTURED_PROJECT_TITLE = (
    r'\A\s*(?:(?:разработк\w*|доработк\w*|редизайн|дизайн|верстк\w*)\s+'
    + PROJECT_RESULT + r'|сайт[- ]визитк\w*\b)'
)
WEB_BRIEF_DETAIL = (
    r'\bпо\s+(?:готов\w*\s+)?(?:макет\w*|прототип\w*|тз)\b'
    r'|\b(?:в|из)\s+(?:figma|фигм\w*)\b'
    r'|\b(?:на\s+(?:двух|трех)\s+язык\w*|языки\s*(?::|[-–—]|\s))'
    r'|\b(?:форм\w*\s+(?:обратн\w*\s+связ\w*|заявк\w*)|навигаци\w*|адаптив\w*|десктоп\w*)\b'
    r'|\b(?:решени\w*|задач\w*)\s*[:—]?\s*(?:полностью\s+)?(?:обнов\w*|собра\w*|добав\w*|сдела\w*)\b'
    r'|\b(?:добавить|обновить|поменять|исправить|заменить)\s+'
    r'(?:(?:\d+|два|две|три|нов\w*|текущ\w*)\s+)?(?:блок\w*|раздел\w*|страниц\w*|контакт\w*|кнопк\w*)\b'
)
PROJECT_DISCUSSION = (
    r'\b(?:как\s+[^.!?;]{0,60}\b(?:самому|самостоятельно)|'
    r'(?:поделитесь|подскажите|посоветуйте)\s*,?\s+(?:как|какие|какой))\b'
)
CUSTOMER_ACQUISITION_PURPOSE = (
    r'\b(?:сайт\w*|лендинг\w*)\s*,?\s+'
    r'(?:для\s+(?:привлечени\w*|поиск\w*)|чтобы\s+(?:искать|найти|привлекать))\s+'
    r'(?:(?:нов\w*|тепл\w*|потенциальн\w*)\s+)?(?:клиент\w*|лид(?:а|ы|ов))\b'
)
DIRECT_PROJECT = (
    r'\b(?:нуж(?:ен|на|но|ны)|создать|сделать|собрать|разработать|сверстать|доработать|переделать|'
    r'редизайн|разработка|создание|доработка|верстка)\s+(?:(?:нов(?:ый|ого|ую|ая|ые|ых|ое)|отдельн\w*|наш\w*|действующ\w*)\s+)?'
    r'(?:сайт\w*|лендинг\w*|интерфейс\w*|презентаци\w*)\b'
)
SEPARATE_WEB_TASK = (
    r'\bотдельно\s+(?:(?:нужно|надо)\s+)?(?:' + DIRECT_PROJECT
    + r'|(?:(?:ищ\w*|нуж\w*|требу\w*)\s+)?(?:веб[- ]?)?(?:дизайнер\w*|разработчик\w*|верстальщик\w*)'
    r'(?:\s+для)?\s+(?:сайт\w*|лендинг\w*|интерфейс\w*|презентаци\w*)\b)'
)
ACCOUNT_SUPPORT = r'\b(?:верифицир\w*|подозрительн\w*\s+активност\w*)\b.{0,160}\bподскажите\b'
MIXED_APP_PROJECT = (
    r'\b(?:создани\w*|разработк\w*|создать|сделать|разработать)\s+(?:веб[- ]?)?сайт\w*'
    r'\s*(?:\+|/|\bи\b|\bплюс\b)\s*(?:мобильн\w*\s+)?приложени\w*\b'
)
TEAM_RECRUITMENT = r'\b(?:ищ\w*|набира\w*)\s+(?:в\s+команду\s+)?(?:визуальн\w*\s+|веб[- ]?)?(?:разработчик\w*|дизайнер\w*|специалист\w*)\b'
TEAM_EXPANSION = r'\bдля\s+(?:пополнени\w*|расширени\w*)\s+команд\w*\b'
INTERNAL_TEAM = r'\b(?:внутренн\w*\s+(?:систем\w*|инструмент\w*)|основн\w*\s+команд\w*)\b'
UNPAID_START = r'\bбез\s+фиксированн\w*\s+оплат\w*|\bпо\s+каждому\s+проекту\b.{0,25}\bсдельно\b'
UNPAID_TRIAL = (
    r'\b(?:неоплачиваем\w*|бесплатн\w*)\s+(?:стажировк\w*|испытательн\w*\s+срок\w*)\b'
    r'|\b(?:стажировк\w*|испытательн\w*\s+срок\w*)\s+(?:без\s+оплат\w*|не\s+оплачива\w*)\b'
)

SITE_NOUN = r'(?:сайт\w*|лендинг\w*|тильд\w*|tilda)\b'
SITE_MODIFIER = (
    r'(?:(?:наш\w*|ваш\w*|будущ\w*|существующ\w*|действующ\w*|'
    r'нов\w*|стар\w*|корпоративн\w*|интернет[- ]магазин\w*)\s+){0,2}'
)
SITE_CONTEXT = re.compile(
    r'\b(?:для|к|со?|через|у|домен\w*)\s+' + SITE_MODIFIER + SITE_NOUN
    + r'|\b(?:на|в)\s+' + SITE_MODIFIER + r'(?:сайте|сайтах|лендинге|лендингах|тильде|tilda)\b'
)
MIGRATION_TO_TILDA = (
    r'\bперенест\w*\s+(?:(?:готов\w*|существующ\w*)\s+)?(?:сайт\w*|проект\w*|макет\w*|его)'
    r'(?:\s+с\s+(?:платформ\w*\s+)?(?P<origin>[\w.]+))?\s+(?:в|на)\s+(?:tilda|тильд\w*)\b'
)
DESIGN_PLATFORM_CONTEXT = (
    r'\bсайт\w*\s+(?:(?:сделан\w*|работа\w*)\s+)?на\s+'
    r'(?:modx|wordpress|вордпресс\w*|битрикс|php|next\.js|opencart|опенкарт\w*)\b'
)
DESIGN_HANDOFF = r'\bдля\s+дальнейш\w*\s+интеграци\w*\s+на\s+сайт\w*\b'
SITE_EDIT = (
    r'\b(?:добав\w*|поменя\w*|измен\w*|обнов\w*|замен\w*|исправ\w*|поправ\w*|настро\w*|удал\w*)\s+'
    r'(?:(?:на|в)\s+' + SITE_MODIFIER + SITE_NOUN + r'\s+)?'
    r'(?:(?:нов\w*|текущ\w*|существующ\w*)\s+){0,2}'
    r'(?:блок\w*|страниц\w*|форм\w*|кнопк\w*|меню|раздел\w*|текст\w*|фотографи\w*|заголов\w*|иерархи\w*)\b'
    r'|\b(?:кнопк\w*|форм\w*).{0,40}\bне\s+(?:нажимает\w*|отправля\w*|работа\w*)\b'
    r'|\bоформ(?:лени\w*|ить)\s+(?:готов\w*\s+)?кейс\w*\b'
    r'|\b(?:есть\s+окно|дизайнер\w*.{0,35}сотрудничеств\w*).{0,60}\bдля\s+лендинг\w*\b'
    r'|' + MIGRATION_TO_TILDA
)


def contextual_website_requests(text, requests, website_pattern):
    """A destination or platform mention alone does not establish a web deliverable."""
    has_context = False
    for _, stop, services, start in requests:
        if set(services) != {'website'}:
            return False
        scope = text[start:stop]
        if positive(SITE_EDIT, scope):
            return False
        context = [match.span() for match in SITE_CONTEXT.finditer(scope)]
        for match in positive(website_pattern, scope):
            if not any(a <= match.start() and match.end() <= b for a, b in context):
                return False
            has_context = True
    return has_context


def requested_outside_matches(pattern, text):
    """Exclude known work the author explicitly keeps on their side."""
    kept_by_author = r'^.{0,35}(?:делаем\s+сами|(?:берем|возьмем)\s+на\s+себя|на\s+нашей\s+стороне)\b'
    matches = positive(pattern, text)
    return [match for match in matches
            if not re.match(kept_by_author, re.split(r'[.!?;]', text[match.end():], maxsplit=1)[0])]


def select(text,profile):
    s,signals,clauses=facts(text)
    frames=extract(text)
    patterns={k:v.get('match_pattern',v['pattern']) for k,v in profile['services'].items()}
    def services(t):return [k for k,p in patterns.items() if positive(p,t)]
    all_services=services(s)
    for spec in profile.get('contextual_results',[]):
        if spec['context'] in all_services:
            patterns[spec['service']]+='|'+spec['pattern']
    all_services=services(s)
    evidence=[quote(text,m.start(),m.end(),k,'role.'+k) for k,ms in signals.items() for m in ms]
    evidence.extend(quote(text,m.start(),m.end(),'topic.'+k,'profile.mention') for k,p in patterns.items() for m in positive(p,s))
    evidence.extend(quote(text,m.start(),m.end(),'intent.'+k,'intent.'+k) for k,ms in frames.items() for m in ms)
    out={'kind':'other','queue':'none','task_exists':None,'service_fit':'unknown','author_role':'unknown',
      'services':[],'need':'Задача для исполнителя не подтверждена','reason':'Подходящий запрос не подтверждён',
      'work_direction':'unknown','cooperation_format':'unknown','constraints':[],'unknown_details':[],
      'evidence':evidence,'rule_ids':[],'requested_result':None}
    def done(kind,route,reason,rule,task=None,fit='unknown',role='unknown'):
        out.update(kind=kind,queue=route,reason=reason,task_exists=task,service_fit=fit,author_role=role)
        out['rule_ids'].append(rule);return out
    requests=[];outside=[]
    for a,b in clauses:
        part=s[a:b];found=services(part)
        for m in positive(REQUEST,part):
            tail=part[m.end():];local=services(tail)
            target=positive(profile['outside_target_pattern'],tail)
            if target and target[0].start()<8 and not services(tail[:target[0].start()]) and not re.search(r'(?:и|также|отдельно).{0,15}(?:разработчик|верстальщик|веб.?дизайнер)',tail):
                t=target[0];evidence.append(quote(text,a+m.end()+t.start(),a+m.end()+t.end(),'outside_result','profile.outside_target'))
                outside.append((a+m.start(),b));continue
            if not found or re.match(r'\s*(?:работу|заказы|клиентов|партнер\w*\s+по\s+продаж)',tail):continue
            if not local:
                if not re.search(r'сверст|передел|исправ|доработ|собра|созда|дизайнер|разработчик|верстальщик|передад|есть\s+заказчик|нужн',part):continue
                local=found
            requests.append((a+m.start(),b,local,a))
    # A desired result, price bid, transfer, or availability question can be a
    # request without "need/search". Context here is this same saved message.
    frame_requests=frames['seek_executor']+frames['desired_action']+frames['incoming_condition']+frames['actual_relay']+frames['incoming_sales']
    system_requests = positive(SYSTEM_REQUEST, s)
    if 'automation' in all_services:
        frame_requests += system_requests
    structured_order = bool(positive(r'\b(?:бюджет|раздел\s+fl\.ru)\s*:', s))
    raw = text.lower().replace('ё', 'е')
    project_titles = positive(PROJECT_TITLE, raw)
    if structured_order or (positive(WEB_BRIEF_DETAIL, s) and not positive(PROJECT_DISCUSSION, s)):
        project_titles += positive(STRUCTURED_PROJECT_TITLE, raw)
    # A question about a named result is not a standalone assignment.
    project_titles = [title for title in project_titles
                      if not re.match(r'[^.!?;\r\n]*\?', raw[title.end():])]
    frame_requests += project_titles
    if frame_requests and all_services:
        for m in frame_requests:
            a,b=next(((a,b) for a,b in clauses if a<=m.start()<b),(0,len(text)))
            bad=positive(profile['outside_target_pattern'],s[m.end():b])
            if bad and bad[0].start()<8 and not services(s[m.end():m.end()+bad[0].start()]) and not re.search(r'отдельно|исполнители\s+могут\s+быть\s+разные',s[m.end():b]):
                outside.append((m.start(),b));continue
            local=services(s[a:b])
            if not local and re.search(r'обновить\s+наш|кто\s+(?:исправит|посмотрит|свободен)|исполнителя\s+выбираем|ищу.{0,15}кому\s+передать|есть\s+кто',m.group()+' '+s[a:b]):local=all_services
            if local:requests.append((m.start(),b,local,a))
    pool=bool(frames['recurring'] or frames['incoming_sales'])
    automation_matches = positive(patterns.get('automation', r'(?!)'), s)
    separate_subcontract = any(not any(a.start() <= m.start() and m.end() <= a.end() for a in automation_matches)
                               for m in signals['pool'])
    if separate_subcontract and not frames['finite_transfer']:pool=True
    if pool and all_services and not requests and (signals['request'] or re.search(r'технические\s+специалисты|подрядчик|дизайнер(?:ов|ы)',s)):
        requests=[(signals['request'][0].start() if signals['request'] else 0,len(text),all_services,0)]
    closed=signals['closed']+frames['closed']+positive(r'\b(?:разработчик|дизайнер|верстальщик)\s+найден\b', s)
    closed.sort(key=lambda m:m.start())
    if closed:requests=[r for r in requests if r[0]>closed[-1].end()]
    providers=positive(r'\b(?:'+profile['provider_pattern']+')',s)
    brief_instructions = positive(r'\((делаем\s+[«"„]?один\s+в\s+один\b)', s) if requests else ()
    instruction_starts = {match.start(1) for match in brief_instructions}
    if structured_order and requests:
        steps = positive(r'\bпосле\s+(?:утверждени\w*|согласовани\w*)\s+(?:стил\w*|макет\w*|вариант\w*)\s*[,—-]?\s*(делаем)\b', s)
        instruction_starts.update(match.start(1) for match in steps)
    providers = [match for match in providers if match.start() not in instruction_starts]
    evidence.extend(quote(text,m.start(),m.end(),'offer','profile.self_promotion') for m in providers)
    advertisement=bool(signals['offer'] or providers or frames['outgoing_condition'] or frames['seek_work'] or positive(SERVICE_AD,s))
    independent=bool(re.search(r'отдельно\s+(?:нуж|ищ)|для\s+(?:своего|моего|нашего)\s+(?:сайта|проекта)',s))
    own_request=re.search(r'(?:я|мы)[^.!?]{0,30}(?:дизайнер|разработчик)[^.!?]{0,35}(?:сейчас\s+)?(?:нуж|ищ)',s)
    independent |= bool(own_request and not re.search(r'если\s+(?:вам|вашему)',own_request.group()))
    independent |= bool(frames['independent'] or frames['actual_relay'] or frames['incoming_condition'])
    if positive(INFORMATION_POST, s) and not independent:
        return done('other','none','Инструкция, обсуждение или объявление без заказа','context.information_post',False,'outside')
    if frames['narrative'] and not frames['actual_relay']:
        return done('other','none','Обсуждение / цитируемый пример, а не текущая передача работ','context.discussion',False,'outside')
    if frames['instruction_question'] and not frames['seek_executor']:
        return done('ambiguous','review','Вопрос о самостоятельном решении / рынке; заказ не подтверждён','context.information_question')
    acquisition_purpose = [match.span() for match in positive(CUSTOMER_ACQUISITION_PURPOSE, s)]
    sales_request = any(
        not any(start <= match.start() and match.end() <= end for start, end in acquisition_purpose)
        for match in signals['sales'] + frames['outgoing_sales'] + positive(LEAD_SEARCH, s)
    )
    if sales_request and not (requests and independent):
        out['work_direction']='clients_to_author'
        return done('other','none','Ищут клиентов / продажи для другого исполнителя','direction.outbound_sales',True,'outside','performer')
    if advertisement and not (requests and independent):
        return done('offer','none','Автор предлагает свои услуги, а не передаёт нам работу','role.self_promotion',False,'outside','performer')
    alternative=bool(re.search(r'в\s+штат.{0,30}(?:или|либо).{0,15}(?:проект|подряд)|проект.{0,25}(?:или|либо).{0,15}в\s+штат',s))
    alternative |= bool(signals['staff'] and re.search(r'не\s+в\s+штат|без\s+трудоустройств|трудоустройств\w*\s+не\s+(?:нуж|треб)',s))
    hire_bundle=bool(re.search(r'рекрутер|открыты\s+вакансии|с\s+вакансией|бонус\w*\s+за\s+переработ',s))
    concrete=bool(requests and any(re.search(r'собра|сверст|созда|исправ|передел|доработ|разов\w*\s+(?:проект|лендинг|сайт|презентаци)|проектн\w*\s+оплат',s[r[0]:r[1]]) for r in requests))
    if alternative:
        out['cooperation_format']='alternatives'
        return done('ambiguous','review','Предлагаются штат или проект; отдельный объём не раскрыт','format.alternatives',True)
    if hire_bundle and not concrete and not signals['staff']:
        return done('ambiguous','review','Подборка вакансий без подтверждённого объёма подходящего проекта','format.hiring_bundle')
    if signals['staff']:
        out['cooperation_format']='employment'
        return done('vacancy','employment','Найм / подбор сотрудников без подтверждённого отдельного проекта','format.employment',True,'outside','recruiter')
    outside_brief = positive(OUTSIDE_BRIEF, s)
    direct_project = bool(project_titles or positive(DIRECT_PROJECT, s)
                          or (system_requests and requests and 'automation' in all_services))
    separate_project = bool(requests and positive(SEPARATE_WEB_TASK, s))
    if not separate_project and positive(UNPAID_TRIAL, s):
        out['cooperation_format']='employment'
        return done('vacancy','employment','Условие работы — неоплачиваемая стажировка или испытательный срок','format.unpaid_trial',True,'outside','recruiter')
    if (not separate_project and positive(TEAM_RECRUITMENT,s) and positive(INTERNAL_TEAM,s)
            and re.search(UNPAID_START,s)):
        out['cooperation_format']='employment'
        return done('vacancy','employment','Набор в команду; отдельный оплачиваемый проект не описан','format.team_recruitment',True,'outside','recruiter')
    if not (concrete or direct_project or separate_project) and positive(TEAM_RECRUITMENT,s) and positive(TEAM_EXPANSION,s):
        out['cooperation_format']='employment'
        return done('vacancy','employment','Пополнение команды; конкретная задача не описана','format.team_expansion',True,'outside','recruiter')
    if (not separate_project and positive(DESIGN_VIDEO_BUNDLE, s) and
            requested_outside_matches(VIDEO_DELIVERABLE, s)):
        return done('ambiguous','review','Общий набор дизайнеров и монтажёров для выпуска материалов','fit.design_video_bundle',True)
    if not (separate_project or direct_project and frames['separable']):
        for pattern, rule, reason in (
            (PROMOTION_TASK, 'fit.promotion', 'Запрошено SEO-продвижение или ведение рекламы'),
            (SALES_WORK, 'fit.sales_work', 'Запрошены продажи, рассылки или поиск клиентов'),
            (CONTENT_WORK, 'fit.content_work', 'Запрошена отдельная работа с контентом или документами'),
            (OPERATIONS_WORK, 'fit.operations_work', 'Запрошены тестирование, данные, действия пользователя или обслуживание'),
            (SOFTWARE_WORK, 'fit.software_work', 'Запрошено отдельное приложение или программное решение'),
            (OTHER_DELIVERABLE, 'fit.other_deliverable', 'Запрошена иллюстрация или отдельная инфраструктурная работа'),
        ):
            matches = requested_outside_matches(pattern, s)
            if rule == 'fit.sales_work':
                matches = [match for match in matches if not any(
                    start <= match.start() and match.end() <= end for start, end in acquisition_purpose)]
            if not matches:
                continue
            match = matches[0]
            evidence.append(quote(text, match.start(), match.end(), 'outside_result', rule))
            if direct_project:
                return done('ambiguous','review','Не подтверждён самостоятельный объём веб-работы','fit.mixed_scope',True)
            return done('other','none',reason,rule,True,'outside')
    if outside_brief and not (separate_project or direct_project and frames['separable']):
        m = outside_brief[0]
        evidence.append(quote(text,m.start(),m.end(),'outside_result','profile.outside_brief'))
        if direct_project:
            return done('ambiguous','review','Отдельный объём работ по сайту или дизайну не подтверждён','fit.mixed_brief',True)
        return done('other','none','Запрошенная работа вне профиля MAXMADE','fit.outside_brief',True,'outside')
    if not direct_project and positive(ACCOUNT_SUPPORT, s):
        return done('ambiguous','review','Вопрос о работе аккаунта или платформы; заказ не подтверждён','context.account_support')
    if positive(MIXED_APP_PROJECT, s) and not (frames['separable'] or separate_project):
        return done('ambiguous','review','Сайт и приложение запрошены вместе; отдельный объём сайта не подтверждён','fit.mixed_app',True)
    if positive(ROLE_WITHOUT_SCOPE, s) and not (direct_project or separate_project or pool):
        return done('ambiguous','review','Ищут специалиста, объём отдельного проекта не описан','task.role_without_scope',True)
    if requests and positive(SCOPE_REVIEW, s) and not separate_project:
        return done('ambiguous','review','Нужны границы веб-работы и проверка компетенций','fit.specialized_scope',True,'needs_verification')
    if system_requests and (requests or not closed) and 'automation' not in all_services and not (direct_project or separate_project):
        return done('ambiguous','review','В запросе на систему нужно уточнить процесс, результат и технологии','task.system_scope_unknown',True)
    if not requests:
        if closed:return done('closed_signal','none','Задача закрыта; нового запроса после закрытия нет','task.closed',False,'outside')
        if outside:return done('other','none','Есть запрос на работу; запрошенная услуга вне профиля MAXMADE','fit.outside',True,'outside','unknown')
        if signals['request'] and positive(profile['outside_result_pattern'],s) and not all_services:
            return done('other','none','Есть запрос на работу; запрошенная услуга вне профиля MAXMADE','fit.outside',True,'outside')
        if not requests and re.search(profile.get('non_work_event_pattern',r'(?!)'),s):
            return done('other','none','Приглашение на мероприятие, не создание материалов','context.event',False,'outside')
        # A clear request for help/performer does not establish the work subject.
        # Keep it locally for clarification, without inventing a service or fee.
        help_request=positive(r'\b(?:помогите|кто\s+поможет)\b.{0,60}\b(?:проект\w*|задач\w*|работ\w*)\b',s)
        requested_tail=s[signals['request'][-1].end():].strip() if signals['request'] else ''
        role_pattern=r'\b(?:помощь|помощник\w*|исполнитель\w*|специалист\w*|человек\w*|дизайнер\w*|разработчик\w*)\b'
        requested_role=positive(role_pattern,requested_tail)
        role_is_target=bool(requested_role and requested_role[0].start()<=10)
        generic_scope=bool(positive(r'\b(?:проект\w*|задач\w*|подробност\w*|детал\w*)\b',requested_tail))
        bare_role=bool(re.fullmatch(r'(?:'+role_pattern+r')[\s.!?]*',requested_tail))
        unknown_subject=bool(help_request or (signals['request'] and
            ((role_is_target and (generic_scope or bare_role)) or positive(r'\bдизайн(?:ер\w*)?\b',requested_tail))))
        if not all_services and signals['request']:
            tail=s[signals['request'][-1].end():]
            complement=re.match(r'\s*(?:помощь|поддержка)\s+',tail)
            if complement:
                target=positive(profile['outside_target_pattern'],tail[complement.end():])
                if target and target[0].start()==0:
                    return done('other','none','Есть запрос на работу; запрошенная услуга вне профиля MAXMADE','fit.outside',True,'outside')
        if not all_services and (unknown_subject or frames['incoming_sales']):
            if help_request:
                m=help_request[0];evidence.append(quote(text,m.start(),m.end(),'request','task.scope_unknown'))
            out.update(need=text.strip()[:350],requested_result=text.strip()[:350],work_direction='work_to_us',
                       unknown_details=['Предмет работ и соответствие профилю MAXMADE не установлены'])
            return done('ambiguous','review','Есть запрос на работу, но предмет работ неизвестен; сначала уточнить задачу','task.scope_unknown',True)
        if frames['reciprocal'] or re.search(r'партнер|подрядчик|техническ\w*\s+специалист',s):
            out['unknown_details']=['Не установлено, передают ли работу нам или ожидают клиентов / другие действия от нас']
            return done('ambiguous','review','Неизвестно направление сотрудничества: передача работы нам не подтверждена','direction.unknown')
        if all_services or frames['incoming_sales'] or frames['reciprocal'] or (signals['request'] and positive(profile['unconfirmed_competence_pattern'],s)) or re.search(r'дизайнер|разработчик|партнер|подрядчик|техническ\w*\s+специалист|кнопка\s+заявки|\b(?:мне\s+тоже|актуально|уже\s+нашли)\b',s) or any(re.search(r'\b'+re.escape(t)+r'\b',s,re.I) for t in profile.get('confirmed_tools',[])):
            return done('ambiguous','review','Запрос на выполнение работ не подтверждён: недостаточно контекста сообщения','task.insufficient')
        return done('other','none','Нет запроса на выполнение работ','fit.no_request',False,'outside')
    requests.sort(key=lambda r:r[0]);start,stop,chosen,clause_start=requests[-1]
    if re.search(r'детали\s+в\s+личке',s) and not re.search(r'лендинг|\bсайт\b|презентаци|интерфейс|ui.kit',s):
        return done('ambiguous','review','Есть запрос на работу, но предмет и формат скрыты; сначала уточнить задачу','task.undisclosed_scope',True)
    out.update(services=list(dict.fromkeys(k for r in requests for k in r[2])),need=text[clause_start:stop].strip(),
       requested_result=text[clause_start:stop].strip(),work_direction='work_to_us',cooperation_format='ongoing_subcontract' if pool else 'project')
    for k in out['services']:
        for m in positive(patterns[k],s):evidence.append(quote(text,m.start(),m.end(),'service.'+k,'profile.service.'+k))
    if re.search(r'не\s+(?:пишите|откликайтесь)|отклики\s+не\s+принима',s[clause_start:]):
        return done('closed_signal','none','Автор запретил отклики на текущую задачу','contact.prohibited',True,'outside')
    if 'website' in patterns and contextual_website_requests(s, requests, patterns['website']):
        return done('ambiguous','review','Сайт упомянут в контексте; конкретная веб-задача не подтверждена','fit.context_only',True)
    required_platform = positive(profile.get('required_existing_platform_pattern',r'(?!)'),s)
    design_only = ('interface' in out['services'] and
                   (positive(r'\bтолько\s+(?:дизайн\w*|макет\w*)\b', s) or positive(DESIGN_HANDOFF, s))
                   and not positive(r'\b(?:сверстать|запрограммировать|доработать\s+код)\b', s))
    if design_only:
        design_platforms = {match.span() for match in positive(DESIGN_PLATFORM_CONTEXT, s)}
        required_platform = [match for match in required_platform if match.span() not in design_platforms]
    migration_to_tilda = positive(MIGRATION_TO_TILDA, s)
    if required_platform and not migration_to_tilda:
        m = required_platform[0]
        constraint = quote(text,m.start(),m.end(),'constraint','profile.required_platform')
        out['constraints'].append(constraint)
        evidence.append(constraint)
        return done('demand','review','Обязательная платформа требует проверки компетенции','fit.required_platform',True,'needs_verification')
    role='agency' if re.search(r'\b(?:мы|наше)\s+агентство|\bстудия\b',s) else 'intermediary' if re.search(r'есть\s+заказчик|сам\s+не\s+успеваю|передадим|передаем',s) else 'unknown'
    kind='partnership' if pool else 'demand';route='partnership' if pool else 'main'
    fit='matched';reason='Ищут исполнителя для подходящей задачи MAXMADE'
    scope_start=start if re.search(r'так\s*же|также|отдельно|а\s+еще|реклама\s+уже|уже\s+(?:идет|есть|сделали)',s[clause_start:start]) else clause_start
    if any(a<start for a,b in outside):scope_start=start
    task_text=s[scope_start:stop] if not pool else s
    unsupported=positive(profile['unconfirmed_competence_pattern'],task_text)
    context_platforms = [match.span('origin') for match in positive(MIGRATION_TO_TILDA,task_text) if match['origin']]
    if design_only:
        context_platforms.extend(match.span() for match in positive(DESIGN_PLATFORM_CONTEXT, task_text))
        context_platforms.extend(match.span() for match in positive(DESIGN_HANDOFF, task_text))
    unsupported = [match for match in unsupported if not any(
        start <= match.start() and match.end() <= end for start, end in context_platforms)]
    outside_mix=positive(profile['outside_result_pattern'],task_text)
    # A word such as "cover" names a different result only when it is not
    # explicitly a component of the requested website in this same scope.
    contextual_spans=[m.span() for spec in profile.get('contextual_scope_exceptions',[]) for m in positive(spec['pattern'],task_text)]
    outside_mix=[m for m in outside_mix if not any(a<=m.start() and m.end()<=b for a,b in contextual_spans)]
    separable=bool(frames['separable']) or pool or bool(re.search(r'можно\s+(?:взять\s+)?только|только\s+сайт|отдельно|не\s+обязател',s[clause_start:]))
    if unsupported or outside_mix:
        fit='partial' if separable else 'needs_verification'
        for m in unsupported+outside_mix:
            offset=0 if pool else scope_start
            q=quote(text,offset+m.start(),offset+m.end(),'constraint','profile.unconfirmed_or_outside')
            out['constraints'].append(q);evidence.append(q)
        if not separable:route='review';reason='Полный объём / обязательная технология требует проверки компетенций'
        else:reason='Подходит выделенная часть работ; другие услуги и технологии не подтверждены'
    out['unknown_details']=['Отсутствующие бюджет, срок, платформа и маршрут остаются неизвестными']
    if '[Фрагмент сокращён;' in text or len(text)>2000:
        route='review';reason='Сохранён неполный текст: сначала проверить полный объём';out['rule_ids'].append('input.truncated')
    return done(kind,route,reason,'task.incoming_subcontract' if pool else 'task.concrete_request',True,fit,role)
