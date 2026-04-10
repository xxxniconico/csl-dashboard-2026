!function(e,t){"object"==typeof exports&&"undefined"!=typeof module?module.exports=t(require("pinia"),require("vue")):"function"==typeof define&&define.amd?define(["pinia","vue"],t):(e="undefined"!=typeof globalThis?globalThis:e||self).NuwaComponentCFLSponsor=t(e.Pinia,e.Vue3)}(this,function(e,t){"use strict";var o=Object.defineProperty,l=Object.getOwnPropertySymbols,n=Object.prototype.hasOwnProperty,r=Object.prototype.propertyIsEnumerable,a=(e,t,l)=>t in e?o(e,t,{enumerable:!0,configurable:!0,writable:!0,value:l}):e[t]=l,i=(e,t)=>{for(var o in t||(t={}))n.call(t,o)&&a(e,o,t[o]);if(l)for(var o of l(t))r.call(t,o)&&a(e,o,t[o]);return e};const c=(e,t,o)=>{const{transData:l}=o.ctx.appContext.config.globalProperties,n=t.oldValue,r=t.value;let a=l.rbkeyConfig?l.rbkeyConfig[r]:"";if(t.arg){const e=t.arg[r];e&&Object.keys(e).forEach(t=>{a=a.replace(t,e[t])})}e.innerHTML!=a&&(e.innerHTML=a),n!=r&&e.setAttribute("data-tkey",r)},s={ssrRender:()=>null,mounted(e,t,o){c(e,t,o)},beforeUpdate(e,t,o){c(e,t,o)},getSSRProps:e=>({"t-k":e.value,"data-tkey":e.value})};class d{constructor(){this.app=null,this.id=""}init(o,l){this.app=function(o,l){const n=t.createSSRApp(o);n.config.globalProperties.transData=(null==l?void 0:l.data)||l;const r=e.createPinia();return n.use(r),n.directive("t",s),n}(o,l),this.id=(null==l?void 0:l.id)?`div[id="${null==l?void 0:l.id}"]`:'div[id="app"]',this.app.mount(document.querySelector(this.id))}unmount(){this.app.unmount(),this.app=null,this.id=""}setId(e){flash_fe_core_tool.$event_publisher.broadcast("setId",e)}}const u={seriesData:[{leagueName:{t_id:"中超联赛",language:{en:"Super League",zh:"中超联赛"}},leagueImage:{t_id:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-csl.png",language:{en:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-csl.png",zh:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-csl.png"}},children:[{titleText:{t_id:"官方冠名商",language:{en:"Official Title Sponsor",zh:"官方冠名商"}},children:[{url:"",logoImg:{id:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-yibao.png",t_id:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-yibao.png",language:{en:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-yibao.png"}}}]},{titleText:{t_id:"官方战略合作伙伴",language:{en:"Official Strategic Partners",zh:"官方战略合作伙伴"}},children:[{url:"",logoImg:{id:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-nike.png",t_id:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-nike.png",language:{en:"//uat-pic.cfl-china.cn/cfluat/footer/icon-sponsor-nike.png"}}}]}]}]},p={class:"leagues-container"},g={class:"league-banner"},v=["src","alt"],m={class:"league-text"},f={class:"league-name"},h={class:"league-name-en"},b={key:0,class:"sponsor-columns"},y={class:"col-title"},k={class:"zh"},_={class:"en"},E={class:"col-logos"},w=["href","target"],B=["src","alt"],S={key:1,class:"sponsor-columns mobile-logos"},x={class:"col-logos"},N=["href"],V=["src","alt"],z=((e,t)=>{const o=e.__vccOpts||e;for(const[l,n]of t)o[l]=n;return o})({__name:"pc",setup(e){const{appContext:o}=t.getCurrentInstance(),l=o.config.globalProperties.transData||{},n=t.computed(()=>l.commonStyle||{}),r=t.computed(()=>{var e,t;return(null==(t=null==(e=o.config.globalProperties.transData)?void 0:e.configData)?void 0:t.componentOptions)||u}),a=t.computed(()=>{var e;return null==(e=o.config.globalProperties.transData)?void 0:e.mobileStyle}),c=t.ref("zh"),s=t.ref(!1),d=()=>{const e=window.innerWidth||document.documentElement.clientWidth||0;s.value=e<=750};t.onMounted(()=>{var e;c.value=(null==(e=document.querySelector('meta[name="language"]'))?void 0:e.getAttribute("content"))||sessionStorage.getItem("viewLanguage")||"zh",d(),window.addEventListener("resize",d)}),t.onUnmounted(()=>{window.removeEventListener("resize",d)});const z=t.ref("normalStyle"),I=t.ref({});t.watch(z,e=>{I.value=(e=>{var t,o,l;const r={};for(const i in n.value)"string"==typeof n.value[i]&&(r[i]=n.value[i]);let a=r;a=i(i({},r),"hoverStyle"===e?null==(t=n.value)?void 0:t.Hover:"pressStyle"===e?null==(o=n.value)?void 0:o.Pressed:null==(l=n.value)?void 0:l.Normal);const c=["border-top-width","border-bottom-width","border-left-width","border-right-width"];return a["border-width"]&&c.forEach(e=>{a[e]="0px"!==a[e]&&a[e]?a[e]:a["border-width"]}),a["border-left"]=`${a["border-left-width"]} solid ${a["border-color"]}`,a["border-right"]=`${a["border-right-width"]} solid ${a["border-color"]}`,a["border-bottom"]=`${a["border-bottom-width"]} solid ${a["border-color"]}`,a["border-top"]=`${a["border-top-width"]} solid ${a["border-color"]}`,a["box-shadow"]=`${a["boxshadow-x"]} ${a["boxshadow-y"]} ${a["boxshadow-blur"]} ${a["boxshadow-color"]}`,a})(e)},{immediate:!0});const $=e=>{if(!Array.isArray(e))return[];const t=[];return e.forEach(e=>{e&&Array.isArray(e.children)&&e.children.forEach(e=>t.push(e))}),t};return(e,o)=>{var l;return t.openBlock(),t.createElementBlock("div",{class:"cfl-sponsor",style:t.normalizeStyle(s.value?i(i({},I.value),a.value):I.value)},[t.createElementVNode("div",p,[(t.openBlock(!0),t.createElementBlock(t.Fragment,null,t.renderList(null==(l=r.value)?void 0:l.seriesData,(e,o)=>{var l,n,r,a,i,d,u;return t.openBlock(),t.createElementBlock("div",{class:"league-row",key:o},[t.createElementVNode("div",g,[t.createElementVNode("img",{class:"league-logo-img",src:s.value?(null==(l=e.leagueImageMobile)?void 0:l.t_id)||(null==(n=e.leagueImage)?void 0:n.t_id):null==(r=e.leagueImage)?void 0:r.t_id,alt:null==(a=e.leagueName)?void 0:a.t_id},null,8,v),t.createElementVNode("div",m,[t.createElementVNode("div",f,t.toDisplayString(null==(i=e.leagueName)?void 0:i.t_id),1),t.createElementVNode("div",h,t.toDisplayString("zh"===c.value?null==(d=e.leagueName)?void 0:d.language.en:null==(u=e.leagueName)?void 0:u.language.zh),1)])]),s.value?(t.openBlock(),t.createElementBlock("div",S,[t.createElementVNode("div",x,[(t.openBlock(!0),t.createElementBlock(t.Fragment,null,t.renderList($(e.children),(e,o)=>{var l,n,r;return t.openBlock(),t.createElementBlock("a",{key:"csl-m-"+o,class:"logo-wrap",href:(null==(l=e.jumpUrl)?void 0:l.t_id)||"javascript:void(0)",target:"_blank",rel:"noopener noreferrer"},[t.createElementVNode("img",{class:"sponsor-logo",src:null==(n=e.logoImg)?void 0:n.t_id,alt:null==(r=e.titleText)?void 0:r.t_id},null,8,V)],8,N)}),128))])])):(t.openBlock(),t.createElementBlock("div",b,[(t.openBlock(!0),t.createElementBlock(t.Fragment,null,t.renderList(e.children,(e,o)=>{var l,n,r;return t.openBlock(),t.createElementBlock("div",{key:"spnosor-"+o,class:"sponsor-col"},[t.createElementVNode("div",y,[t.createElementVNode("div",k,t.toDisplayString(null==(l=e.titleText)?void 0:l.t_id),1),t.createElementVNode("div",_,t.toDisplayString("zh"===c.value?null==(n=e.titleText)?void 0:n.language.en:null==(r=e.titleText)?void 0:r.language.zh),1)]),t.createElementVNode("div",E,[(t.openBlock(!0),t.createElementBlock(t.Fragment,null,t.renderList(e.children,(l,n)=>{var r,a,i,c;return t.openBlock(),t.createElementBlock("a",{key:"spnosor-"+o+"-"+n,class:"logo-wrap",href:(null==(r=l.jumpUrl)?void 0:r.t_id)?l.jumpUrl.t_id:"javascript:void(0)",target:(null==(a=l.jumpUrl)?void 0:a.t_id)?"_blank":"_self",rel:"noopener noreferrer"},[t.createElementVNode("img",{class:"sponsor-logo",src:null==(i=l.logoImg)?void 0:i.t_id,alt:null==(c=e.titleText)?void 0:c.t_id},null,8,B)],8,w)}),128))])])}),128))]))])}),128))])],4)}}},[["__scopeId","data-v-e78c1b41"]]);return class{constructor(e){this.data=e}init(){this.instance=new d,this.instance.init(z,this.data)}update(e){this.instance&&(this.instance.unmount(),this.instance=null),this.data=e,this.init()}}});

document.addEventListener('DOMContentLoaded', function () {
    // console.log('Footer DOM加载完成，开始初始化');
    
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
        
        return function(...args) {
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
    
   /*  // 创建节流版本的setRemUnit函数
    const throttledSetRemUnit = throttle(setRemUnit, 100); // 100ms节流
    
    // 页面加载时设置rem
    setRemUnit();
    
    // 监听窗口大小变化，使用节流版本
    window.addEventListener('resize', throttledSetRemUnit);
    
    // 监听屏幕方向变化，使用节流版本
    window.addEventListener('orientationchange', function() {
        setTimeout(throttledSetRemUnit, 100); // 延迟执行，确保方向变化完成
    }); */
    
    // ========= Footer特定功能 =========
    
    // 社交媒体图标悬停效果
    function initSocialMediaHover() {
        const socialItems = document.querySelectorAll('.social_item');
        
        socialItems.forEach(item => {
            const defaultIcon = item.querySelector('.social_icon_default');
            const hoverIcon = item.querySelector('.social_icon_hover');
            
            if (defaultIcon && hoverIcon) {
                item.addEventListener('mouseenter', function() {
                    defaultIcon.style.opacity = '0';
                    hoverIcon.style.opacity = '1';
                });
                
                item.addEventListener('mouseleave', function() {
                    defaultIcon.style.opacity = '1';
                    hoverIcon.style.opacity = '0';
                });
            }
        });
    }
    
    // 初始化社交媒体悬停效果
    initSocialMediaHover();
    
    // 移动端检测函数
    function isMobile() {
        return window.matchMedia && window.matchMedia('(max-width: 750px)').matches;
    }
    
    // 移动端特殊处理
    function handleMobileLayout() {
        if (isMobile()) {
            // console.log('Footer - 移动端布局激活');
            
            // 移动端可以添加特殊的交互效果
            const footerGroups = document.querySelectorAll('.footer_group');
            footerGroups.forEach(group => {
                group.style.transition = 'all 0.3s ease';
            });
        } else {
            console.log('Footer - PC端布局激活');
        }
    }
    
    // 初始化布局处理
    handleMobileLayout();
    
    // 创建节流版本的布局处理函数
    const throttledHandleMobileLayout = throttle(handleMobileLayout, 100); // 100ms节流
    
    // 监听窗口变化，使用节流版本重新处理布局
    window.addEventListener('resize', throttledHandleMobileLayout);
    
    console.log('Footer 初始化完成');
});


