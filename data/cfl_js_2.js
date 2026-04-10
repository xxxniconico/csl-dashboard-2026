

document.addEventListener('DOMContentLoaded', function () {
    console.log('DOM加载完成，开始初始化');
    // 兜底：某些语言页头模板可能缺失 pc-icons，这里动态补齐
    try {
        const menuElement = document.querySelector('#BASD_head_id .menu-box');
        if (!menuElement) {
            console.warn('未找到菜单元素');
            return;
        }
        menuElement.addEventListener('click', function() {
            const url = this.dataset.jumpurl; // 使用this指向当前点击元素
            if (url) {
                window.location.href = url;
            } else {
                console.warn('jumpurl属性为空或未定义');
            }
        });

        var activeLang = document.querySelector('meta[name="language"]').getAttribute('content');
        let pageName = document.querySelector('meta[name="title"]').getAttribute('content');
        const elementsPageTitle = document.querySelectorAll('.page-title-text');
        elementsPageTitle.forEach(element => {
            element.textContent = pageName;
        });
        const hasChildrenLinks = document.querySelectorAll('.m-nav-link-haschildren');
        const allParentItems = document.querySelectorAll('.m-nav-item-haschildren');
        // 点击菜单项时的处理
        hasChildrenLinks.forEach(link => {
            link.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();

                const currentParent = this.closest('.m-nav-item-haschildren');
                if (!currentParent) return;

                // 移除所有其他父元素的isActive类
                allParentItems.forEach(item => {
                    if (item !== currentParent) {
                        item.classList.remove('isActive');
                    }
                });

                // 切换当前父元素的isActive类
                currentParent.classList.toggle('isActive');
            });
        });
        function ensurePcIcons() {
            var headInner = document.querySelector('.BASD_head_inner');
            if (!headInner) return;
            var exists = headInner.querySelector('.pc-icons');
            if (exists) return;
            // 仅在PC端注入
            var isPc = !(window.matchMedia && window.matchMedia('(max-width: 750px)').matches);
            if (!isPc) return;
            var beforeNode = headInner.querySelector('.m-icons');
            var div = document.createElement('div');
            div.className = 'pc-icons';
            div.style.display = 'flex';
            div.style.gap = '16px';
            div.style.alignItems = 'center';
            div.style.marginLeft = 'auto';
            div.style.marginRight = '60px';
            div.innerHTML = '\n                <a class="pc-icon-btn pc-download" href="javascript:void(0)" onclick="BASD_handleDownload()" title="下载">\n                    <img src="https://pic.cfl-china.cn/cfluat/home/header/icon-download.png" alt="download" style="width:24px;height:24px;display:block;"/>\n                </a>\n                <a class="pc-icon-btn pc-lang" href="javascript:void(0)" onclick="BASD_toggleLang()" title="切换语言">\n                    <img id="BASD_lang_icon" src="https://pic.cfl-china.cn/cfluat/home/header/icon-english.png" alt="lang" style="width:24px;height:24px;display:block;"/>\n                </a>';
            if (beforeNode && beforeNode.parentNode === headInner) {
                headInner.insertBefore(div, beforeNode);
            } else {
                headInner.appendChild(div);
            }
            // 等待模板脚本注册全局方法后再更新图标
            setTimeout(function () {
                if (typeof BASD_updateLangIcon === 'function') BASD_updateLangIcon();
            }, 0);
        }
        ensurePcIcons();
    } catch (e) { console.warn('ensurePcIcons error', e); }

    // ========= 首页检测和Banner显示逻辑 =========
    function checkHomepageByNavText() {
        // 定义首页关键词（可根据实际导航文本调整）
        const homeKeywords = ['首页', '主页', 'Home', 'INDEX', 'index'];
        const navItems = document.querySelectorAll('.BASD_head .content .item');
        const header = document.getElementById('BASD_head_id');
        const banner = document.getElementById('homepage_banner');
        const video = banner.querySelector('.banner-gif');

        let isHomepage = false;
        const currentUrl = window.location.href;
        const currentPath = window.location.pathname;

        if (currentPath.includes('/home.html') ||
            currentPath === '/' ||
            currentPath === '/zh/' ||
            currentPath === '/en/' ||
            currentPath === '/zh' ||
            currentPath === '/en' ||
            currentPath.endsWith('/index.html')) {
            isHomepage = true;
        }

        // 遍历导航项，进行URL匹配和首页检测
        navItems.forEach((item, index) => {
            const titleLink = item.querySelector('.title');
            const navText = item.getAttribute('data-nav-text') || item.textContent.trim();

            // 清除之前的激活状态
            if (titleLink) {
                titleLink.classList.remove('isActive');
            }
            item.classList.remove('active');

            if (titleLink && titleLink.href) {
                const navUrl = titleLink.href;

                // URL完全匹配或包含当前路径
                const urlMatch = currentUrl.includes(navUrl) || navUrl.includes(currentPath);

                if (urlMatch) {
                    titleLink.classList.add('isActive');
                    item.classList.add('active');

                    // 检查激活的导航是否为首页
                    const keywordMatch = homeKeywords.some(keyword =>
                        navText.includes(keyword) || navText.toLowerCase().includes(keyword.toLowerCase())
                    );
                    const urlHomeMatch = navUrl.includes('/home.html');


                    if (keywordMatch || urlHomeMatch) {
                        isHomepage = true;
                    }
                }
            }
        });
        if (header) {
            header.classList.add('BASD_head_' + activeLang);
        }
        // 应用首页样式
        if (isHomepage) {
            if (header) {
                header.classList.add('homepage-transparent');
            }
            if (banner) {
                banner.style.display = 'block';
                banner.classList.add('homepage_banner_' + activeLang);
                if (video.hasAttribute('data-loaded')) {
                    return; // 已经加载过，直接返回
                }

                const videoSrc = banner.getAttribute('data-videosrc') || 'https://pic.cfl-china.cn/cfluat/home/header/defaultvideo.mp4';

                const source = document.createElement('source');
                source.src = videoSrc;
                source.type = 'video/mp4';
                // 清空现有内容并添加新的source
                video.innerHTML = '';
                video.appendChild(source);
                // 加载视频
                video.load();
                video.setAttribute('data-loaded', 'true');
                // 尝试播放（由于有muted属性，通常可以自动播放）
                video.play().catch(e => {
                    console.log('视频自动播放被阻止，需要用户交互:', e);
                });
            }
        } else {
            if (header) {
                header.classList.remove('homepage-transparent');
            }
            if (banner) {
                banner.style.display = 'none';
                video.innerHTML = ''; // 移除source元素
                video.removeAttribute('src'); // 移除直接设置的src（如果有）
                video.load(); // 清除已加载的视频数据
                // 移除加载标记
                video.removeAttribute('data-loaded');
            }
        }

        return isHomepage;
    }

    // 监听导航点击事件
    function handleNavClick() {
        // 延迟执行，确保页面状态更新后再检测
        setTimeout(() => {
            checkHomepageByNavText();
        }, 100);
    }

    // 为所有导航链接添加点击监听
    function bindNavClickEvents() {
        const navLinks = document.querySelectorAll('.BASD_head .content .item .title');
        navLinks.forEach(link => {
            link.addEventListener('click', handleNavClick);
        });
    }

    // 立即执行首页检测
    const initialHomepageCheck = checkHomepageByNavText();

    // 延迟绑定导航事件
    setTimeout(() => {
        bindNavClickEvents();
    }, 100);

    // ========= 移动端rem适配 =========
    function setRemUnit() {
        const designWidth = 750; // 设计稿宽度
        const baseFontSize = 10; // 基础字体大小，1rem = 10px

        // 获取当前屏幕宽度
        const screenWidth = window.innerWidth || document.documentElement.clientWidth || document.body.clientWidth;

        // 计算rem比例
        const remRatio = screenWidth / designWidth;
        const fontSize = baseFontSize * remRatio;

        // 设置根元素字体大小
        document.documentElement.style.fontSize = fontSize + 'px';

    }

    // 节流函数
    function throttle(func, delay) {
        let timeoutId;
        let lastExecTime = 0;

        return function (...args) {
            const currentTime = Date.now();

            if (currentTime - lastExecTime > delay) {
                func.apply(this, args);
                lastExecTime = currentTime;
            } else {
                clearTimeout(timeoutId);
                timeoutId = setTimeout(() => {
                    func.apply(this, args);
                    lastExecTime = Date.now();
                }, delay - (currentTime - lastExecTime));
            }
        };
    }

    // 创建节流版本的setRemUnit函数
    const throttledSetRemUnit = throttle(setRemUnit, 100); // 100ms节流

    // 页面加载时设置rem
    setRemUnit();

    // 监听窗口大小变化，使用节流版本
    window.addEventListener('resize', throttledSetRemUnit);

    // 监听屏幕方向变化，使用节流版本
    window.addEventListener('orientationchange', function () {
        setTimeout(throttledSetRemUnit, 100); // 延迟执行，确保方向变化完成
    });

    // 检查jQuery是否加载，如果没有则使用原生JS
    function $(selector) {
        if (typeof jQuery !== 'undefined') {
            return jQuery(selector);
        }

        // 处理不同类型的参数
        if (!selector) {
            return {
                length: 0,
                each: function () { },
                on: function () { },
                off: function () { },
                click: function () { },
                hover: function () { },
                addClass: function () { },
                removeClass: function () { },
                toggleClass: function () { },
                hasClass: function () { return false; },
                attr: function () { },
                css: function () { },
                html: function () { },
                show: function () { },
                hide: function () { },
                fadeToggle: function () { },
                find: function () { return $([]); },
                closest: function () { return $([]); },
                parent: function () { return $([]); },
                siblings: function () { return $([]); },
                first: function () { return $([]); },
                data: function () { return {}; },
                map: function () { return []; }
            };
        }

        // 如果传入的是DOM元素或元素数组，直接返回包装对象
        if (selector.nodeType || (Array.isArray(selector) && selector.length > 0 && selector[0].nodeType)) {
            const elements = Array.isArray(selector) ? selector : [selector];
            return createWrapper(elements);
        }

        // 如果传入的是字符串选择器
        if (typeof selector === 'string') {
            if (selector === '') {
                return {
                    length: 0,
                    each: function () { },
                    on: function () { },
                    off: function () { },
                    click: function () { },
                    hover: function () { },
                    addClass: function () { },
                    removeClass: function () { },
                    toggleClass: function () { },
                    hasClass: function () { return false; },
                    attr: function () { },
                    css: function () { },
                    html: function () { },
                    show: function () { },
                    hide: function () { },
                    fadeToggle: function () { },
                    find: function () { return $([]); },
                    closest: function () { return $([]); },
                    parent: function () { return $([]); },
                    siblings: function () { return $([]); },
                    first: function () { return $([]); },
                    data: function () { return {}; },
                    map: function () { return []; }
                };
            }
            const elements = document.querySelectorAll(selector);
            return createWrapper(Array.from(elements));
        }

        // 其他情况返回空对象
        return {
            length: 0,
            each: function () { },
            on: function () { },
            off: function () { },
            click: function () { },
            hover: function () { },
            addClass: function () { },
            removeClass: function () { },
            toggleClass: function () { },
            hasClass: function () { return false; },
            attr: function () { },
            css: function () { },
            html: function () { },
            show: function () { },
            hide: function () { },
            fadeToggle: function () { },
            find: function () { return $([]); },
            closest: function () { return $([]); },
            parent: function () { return $([]); },
            siblings: function () { return $([]); },
            first: function () { return $([]); },
            data: function () { return {}; },
            map: function () { return []; }
        };
    }

    // 创建包装对象的辅助函数
    function createWrapper(elements) {
        return {
            length: elements.length,
            each: function (callback) {
                elements.forEach(callback);
            },
            on: function (event, handler) {
                elements.forEach(el => el.addEventListener(event, handler));
                return this;
            },
            off: function (event, handler) {
                elements.forEach(el => el.removeEventListener(event, handler));
                return this;
            },
            click: function (handler) {
                elements.forEach(el => el.addEventListener('click', handler));
                return this;
            },
            hover: function (enterHandler, leaveHandler) {
                elements.forEach(el => {
                    el.addEventListener('mouseenter', enterHandler);
                    el.addEventListener('mouseleave', leaveHandler);
                });
                return this;
            },
            addClass: function (className) {
                elements.forEach(el => el.classList.add(className));
                return this;
            },
            removeClass: function (className) {
                elements.forEach(el => el.classList.remove(className));
                return this;
            },
            toggleClass: function (className) {
                elements.forEach(el => el.classList.toggle(className));
                return this;
            },
            hasClass: function (className) {
                return elements.length > 0 && elements[0].classList.contains(className);
            },
            attr: function (name, value) {
                if (value !== undefined) {
                    elements.forEach(el => el.setAttribute(name, value));
                    return this;
                }
                return elements.length > 0 ? elements[0].getAttribute(name) : undefined;
            },
            css: function (property, value) {
                if (typeof property === 'object') {
                    elements.forEach(el => {
                        Object.assign(el.style, property);
                    });
                } else if (value !== undefined) {
                    elements.forEach(el => el.style[property] = value);
                } else {
                    return elements.length > 0 ? getComputedStyle(elements[0])[property] : undefined;
                }
                return this;
            },
            html: function (content) {
                if (content !== undefined) {
                    elements.forEach(el => el.innerHTML = content);
                } else {
                    return elements.length > 0 ? elements[0].innerHTML : '';
                }
                return this;
            },
            show: function () {
                elements.forEach(el => el.style.display = 'block');
                return this;
            },
            hide: function () {
                elements.forEach(el => el.style.display = 'none');
                return this;
            },
            fadeToggle: function (duration) {
                elements.forEach(el => {
                    const isVisible = el.style.display !== 'none';
                    el.style.display = isVisible ? 'none' : 'block';
                });
                return this;
            },
            find: function (selector) {
                if (!selector) {
                    return $([]);
                }
                const found = [];
                elements.forEach(el => {
                    try {
                        found.push(...el.querySelectorAll(selector));
                    } catch (e) {
                        console.warn('Invalid selector in find:', selector, e);
                    }
                });
                return $(found);
            },
            closest: function (selector) {
                const found = [];
                elements.forEach(el => {
                    const closest = el.closest(selector);
                    if (closest) found.push(closest);
                });
                return $(found);
            },
            parent: function () {
                const found = [];
                elements.forEach(el => {
                    if (el.parentElement) found.push(el.parentElement);
                });
                return $(found);
            },
            siblings: function () {
                const found = [];
                elements.forEach(el => {
                    const siblings = Array.from(el.parentElement.children).filter(child => child !== el);
                    found.push(...siblings);
                });
                return $(found);
            },
            first: function () {
                return elements.length > 0 ? $(elements[0]) : $([]);
            },
            data: function () {
                if (elements.length > 0) {
                    const dataset = elements[0].dataset;
                    const result = {};
                    for (let key in dataset) {
                        result[key] = dataset[key];
                    }
                    return result;
                }
                return {};
            },
            map: function (callback) {
                const results = [];
                elements.forEach((el, index) => {
                    results.push(callback.call(el, index, el));
                });
                return results;
            }
        };
    }
    // 检查元素是否存在再绑定事件
    const $childTitles = $('.BASD_head .child_title');
    if ($childTitles.length > 0) {
        $childTitles.hover(function () {
            const $childList = $('.child_list');
            if ($childList.length > 0) {
                $childList.removeClass('hover');
            }
            const $parents = $(this).parents('.child_list');
            if ($parents.length > 0) {
                $parents.addClass('hover');
            }
        })
    }

    const $subsetBoxes = $('.BASD_head .subset_box');
    if ($subsetBoxes.length > 0) {
        $subsetBoxes.map(function () {
            if (!$(this).find('.child_name_title').length > 0) {
                $(this).addClass('isHorizontal')
            }
        })
    }
    var $targetA = $('.BASD_head .item>a');
    var dataAttributes = $('.BASD_head').data();

    // 基于类名控制样式，去除直接写样式
    if ($targetA.length > 0) {
        $targetA.on('mouseenter', function () {
            $(this).addClass('hover');
            const $parent = $(this).parent();
            if ($parent.length > 0) {
                const $siblings = $parent.siblings();
                if ($siblings.length > 0) {
                    const $siblingLinks = $siblings.find('a');
                    if ($siblingLinks.length > 0) {
                        $siblingLinks.removeClass('hover');
                    }
                }
            }
        }).on('mouseleave', function () {
            $(this).removeClass('hover');
        });

        // 初始化：匹配当前 URL，高亮并让父级 active，常显子菜单
        $targetA.each(function () {
            var href = $(this).attr('href');
            var current = window.location.href.split('#')[0];
            var currentPath = window.location.pathname;


            if (href) {
                // 处理相对地址匹配
                if (href.startsWith('/')) {
                    // 绝对路径匹配
                    if (currentPath === href || currentPath.startsWith(href)) {
                        $(this).addClass('isActive');
                        $(this).closest('.item').addClass('active');
                    }
                } else if (href.startsWith('http')) {
                    // 完整URL匹配
                    if (current.indexOf(href) === 0) {
                        $(this).addClass('isActive');
                        $(this).closest('.item').addClass('active');
                    }
                } else {
                    // 相对路径匹配
                    if (currentPath.includes(href) || current.includes(href)) {
                        $(this).addClass('isActive');
                        $(this).closest('.item').addClass('active');
                    }
                }
            }
        });
    }

    // 二级菜单根据 URL 激活并放大（兼容扁平 oneLevel 结构与旧结构）
    const $subsetTitles = $('.subset_box .subset_item>.name>.title, .subset_box .oneLevel>.title');
    if ($subsetTitles.length > 0) {
        $subsetTitles.each(function () {
            var link = $(this).attr('href');
            var currentUrl = window.location.href;
            var currentPath = window.location.pathname;
            var currentSearch = window.location.search;

            if (link) {
                // 处理相对地址匹配
                if (link.startsWith('/')) {
                    // 绝对路径匹配 - 包含查询参数
                    var fullLink = link;
                    if (fullLink.includes('?')) {
                        // 如果link包含查询参数，需要完整匹配
                        if (currentUrl.includes(fullLink) ||
                            (currentPath + currentSearch) === fullLink) {
                            $(this).addClass('isActive');
                            $(this).closest('.item').addClass('active');
                        }
                    } else {
                        // 如果link不包含查询参数，只匹配路径
                        if (currentPath === link || currentPath.startsWith(link)) {
                            $(this).addClass('isActive');
                            $(this).closest('.item').addClass('active');
                        }
                    }
                } else if (link.startsWith('http')) {
                    // 完整URL匹配
                    if (currentUrl.indexOf(link) === 0) {
                        $(this).addClass('isActive');
                        $(this).closest('.item').addClass('active');
                    }
                } else {
                    // 相对路径匹配
                    if (currentPath.includes(link) || currentUrl.includes(link)) {
                        $(this).addClass('isActive');
                        $(this).closest('.item').addClass('active');
                    }
                }
            }
        });
    }

    if (dataAttributes.position) {
        $('.PC_BASD_head').css('position', 'fixed')
    }
    const $searchImg = $('.BASD_head .search-img');
    if ($searchImg.length > 0) {
        $searchImg.click(function () {
            $('.search-input').fadeToggle(300)
        })
    }
    // 仅切换当前分组的子级，不影响全局
    const $childTitleBtns = $('.child_title_btn');
    if ($childTitleBtns.length > 0) {
        $childTitleBtns.off('click');
        $childTitleBtns.on('click', function (e) {
            e.stopPropagation();
            var $wrapper = $(this).closest('.child_list');
            var $listBox = $wrapper.find('.child_list_box').first();
            var isVisible = $listBox.css('display') === 'block';
            $listBox.css('display', isVisible ? 'none' : 'block');
            $(this).toggleClass('child_title_btn_active');
            $wrapper.find('.child_title').first().toggleClass('child_title_active');
        });
    }

    // ========= 移动端菜单逻辑 =========
    function isMobile() { return window.matchMedia && window.matchMedia('(max-width: 750px)').matches; }
    var $drawer = $('.m-drawer');

    function lockScroll(lock) {
        if (lock) {
            document.body.style.overflow = 'hidden';
            document.body.style.height = '100vh';
        } else {
            document.body.style.overflow = '';
            document.body.style.height = '';
        }
    }
    function openDrawer() {
        if (!$drawer.length) {
            $drawer = $('.m-drawer');
        }
        $drawer.addClass('show').attr('aria-hidden', 'false');
        lockScroll(true);
    }
    function closeDrawer() {
        if (!$drawer.length) {
            $drawer = $('.m-drawer');
        }
        $drawer.removeClass('show').attr('aria-hidden', 'true');
        lockScroll(false);
    }

    // 使用原生事件委托
    document.addEventListener('click', function (e) {
        if (e.target.classList.contains('m-icon-menu-wrapper') || e.target.closest('.m-icon-menu-wrapper')) {
            e.preventDefault();
            openDrawer();
        }
        if (e.target.classList.contains('m-icon-close-wrapper') || e.target.closest('.m-icon-close-wrapper')) {
            e.preventDefault();
            closeDrawer();
        }
        if (e.target.classList.contains('m-drawer-mask')) {
            e.preventDefault();
            closeDrawer();
        }
    });

    // 监听窗口变化，超出移动端范围自动关闭
    window.addEventListener('resize', function () {
        if (!isMobile()) {
            closeDrawer();
        }
    });
});

