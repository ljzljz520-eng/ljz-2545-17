// ======================
// 全局变量
// ======================
let currentSlide = 0;
let slideInterval;

// ======================
// 页面加载完成后执行
// ======================
document.addEventListener('DOMContentLoaded', function() {
    // 初始化导航栏
    initNavbar();
    
    // 初始化轮播图（仅在首页）
    if (document.querySelector('.banner')) {
        initSlider();
    }
    
    // 初始化统计数字动画（仅在首页）
    if (document.querySelector('.stats')) {
        initStatsCounter();
    }
    
    // 初始化课程筛选（仅在课程页）
    if (document.querySelector('.course-filter')) {
        initCourseFilter();
    }
    
    // 初始化学习计划表单（仅在计划页）
    if (document.getElementById('planForm')) {
        initPlanForm();
    }
    
    // 初始化联系表单（仅在联系页）
    if (document.getElementById('contactForm')) {
        initContactForm();
    }
    
    // 初始化FAQ手风琴（仅在联系页）
    if (document.querySelector('.faq-list')) {
        initFAQ();
    }
    
    // 初始化所有按钮点击事件
    initButtonClicks();

    // 初始化个人中心 Tab 切换（仅在个人中心页）
    if (document.querySelector('.profile-tab')) {
        initProfileTabs();
    }
});

// ======================
// 个人中心 Tab 切换
// ======================
function initProfileTabs() {
    const tabs = document.querySelectorAll('.profile-tab');
    const panels = document.querySelectorAll('.profile-panel');

    tabs.forEach(tab => {
        tab.addEventListener('click', function(e) {
            e.preventDefault();
            const target = this.dataset.tab;

            // 更新 tab 高亮
            tabs.forEach(t => t.classList.remove('active'));
            this.classList.add('active');

            // 显示对应面板
            panels.forEach(panel => {
                panel.classList.remove('active');
                if (panel.id === 'panel-' + target) {
                    panel.classList.add('active');
                }
            });
        });
    });
}

// ======================
// 导航栏功能
// ======================
function initNavbar() {
    const navToggle = document.getElementById('navToggle');
    const navMenu = document.getElementById('navMenu');
    
    if (navToggle) {
        navToggle.addEventListener('click', function() {
            navMenu.classList.toggle('active');
        });
    }
    
    // 点击菜单项后关闭移动端菜单
    const navLinks = document.querySelectorAll('.nav-menu a');
    navLinks.forEach(link => {
        link.addEventListener('click', function() {
            if (window.innerWidth <= 768) {
                navMenu.classList.remove('active');
            }
        });
    });
}

// ======================
// 轮播图功能
// ======================
function initSlider() {
    const slides = document.querySelectorAll('.slide');
    const prevBtn = document.getElementById('prevBtn');
    const nextBtn = document.getElementById('nextBtn');
    const dotsContainer = document.getElementById('sliderDots');
    
    // 创建指示点
    slides.forEach((_, index) => {
        const dot = document.createElement('div');
        dot.className = 'dot';
        if (index === 0) dot.classList.add('active');
        dot.addEventListener('click', () => goToSlide(index));
        dotsContainer.appendChild(dot);
    });
    
    // 上一张
    prevBtn.addEventListener('click', function() {
        goToSlide(currentSlide - 1);
    });
    
    // 下一张
    nextBtn.addEventListener('click', function() {
        goToSlide(currentSlide + 1);
    });
    
    // 自动播放
    startSlideShow();
    
    // 鼠标悬停时暂停
    const banner = document.querySelector('.banner');
    banner.addEventListener('mouseenter', stopSlideShow);
    banner.addEventListener('mouseleave', startSlideShow);
}

function goToSlide(n) {
    const slides = document.querySelectorAll('.slide');
    const dots = document.querySelectorAll('.slider-dots .dot');
    
    if (n >= slides.length) {
        currentSlide = 0;
    } else if (n < 0) {
        currentSlide = slides.length - 1;
    } else {
        currentSlide = n;
    }
    
    // 移除所有active类
    slides.forEach(slide => slide.classList.remove('active'));
    dots.forEach(dot => dot.classList.remove('active'));
    
    // 添加active类到当前幻灯片
    slides[currentSlide].classList.add('active');
    dots[currentSlide].classList.add('active');
}

function startSlideShow() {
    slideInterval = setInterval(() => {
        goToSlide(currentSlide + 1);
    }, 5000);
}

function stopSlideShow() {
    clearInterval(slideInterval);
}

// ======================
// 统计数字动画
// ======================
function initStatsCounter() {
    const stats = document.querySelectorAll('.stat-number');
    const statsSection = document.querySelector('.stats');
    let animated = false;
    
    // 检查元素是否在视口中
    const observer = new IntersectionObserver((entries) => {
        entries.forEach(entry => {
            if (entry.isIntersecting && !animated) {
                animated = true;
                animateStats();
            }
        });
    });
    
    observer.observe(statsSection);
}

function animateStats() {
    const stats = document.querySelectorAll('.stat-number');
    
    stats.forEach(stat => {
        const target = parseInt(stat.getAttribute('data-target'));
        const duration = 2000; // 2秒
        const increment = target / (duration / 16); // 每帧增加的值
        let current = 0;
        
        const timer = setInterval(() => {
            current += increment;
            if (current >= target) {
                stat.textContent = target.toLocaleString();
                clearInterval(timer);
            } else {
                stat.textContent = Math.floor(current).toLocaleString();
            }
        }, 16);
    });
}

// ======================
// 课程筛选功能
// ======================
function initCourseFilter() {
    const filterBtns = document.querySelectorAll('.filter-btn');
    const courseItems = document.querySelectorAll('.course-item');
    const searchInput = document.getElementById('searchInput');
    const searchBtn = document.getElementById('searchBtn');
    
    // 分类筛选
    filterBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            // 更新按钮状态
            filterBtns.forEach(b => b.classList.remove('active'));
            this.classList.add('active');
            
            const category = this.getAttribute('data-category');
            filterCourses(category);
        });
    });
    
    // 搜索功能
    searchBtn.addEventListener('click', performSearch);
    searchInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            performSearch();
        }
    });
    
    function filterCourses(category) {
        courseItems.forEach(item => {
            if (category === 'all' || item.getAttribute('data-category') === category) {
                item.style.display = 'flex';
                // 添加淡入动画
                item.style.animation = 'fadeIn 0.5s';
            } else {
                item.style.display = 'none';
            }
        });
    }
    
    function performSearch() {
        const searchTerm = searchInput.value.toLowerCase().trim();
        
        if (searchTerm === '') {
            courseItems.forEach(item => {
                item.style.display = 'flex';
            });
            return;
        }
        
        courseItems.forEach(item => {
            const title = item.querySelector('h3').textContent.toLowerCase();
            const description = item.querySelector('.course-description').textContent.toLowerCase();
            
            if (title.includes(searchTerm) || description.includes(searchTerm)) {
                item.style.display = 'flex';
                item.style.animation = 'fadeIn 0.5s';
            } else {
                item.style.display = 'none';
            }
        });
    }
}

// 添加淡入动画
const style = document.createElement('style');
style.textContent = `
    @keyframes fadeIn {
        from {
            opacity: 0;
            transform: translateY(10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }
`;
document.head.appendChild(style);

// ======================
// 学习计划表单
// ======================
function initPlanForm() {
    const form = document.getElementById('planForm');
    
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        
        // 获取表单数据
        const formData = {
            goal: document.getElementById('learningGoal').value,
            level: document.querySelector('input[name="level"]:checked').value,
            studyTime: document.getElementById('studyTime').value,
            duration: document.getElementById('duration').value
        };
        
        // 验证表单
        if (!validatePlanForm(formData)) {
            return;
        }
        
        // 生成学习计划
        generateLearningPlan(formData);
    });
}

function validatePlanForm(data) {
    if (!data.goal) {
        showMessage('请选择学习目标', 'error');
        return false;
    }
    if (!data.level) {
        showMessage('请选择当前水平', 'error');
        return false;
    }
    if (!data.studyTime) {
        showMessage('请选择每周学习时间', 'error');
        return false;
    }
    if (!data.duration) {
        showMessage('请选择期望完成时间', 'error');
        return false;
    }
    return true;
}

function generateLearningPlan(data) {
    // 显示加载提示
    showMessage('正在生成专属学习计划...', 'info');
    
    // 模拟API请求
    setTimeout(() => {
        const goalMap = {
            'frontend': '前端开发工程师',
            'backend': '后端开发工程师',
            'fullstack': '全栈开发工程师',
            'designer': 'UI/UX设计师',
            'data': '数据分析师',
            'ai': 'AI工程师'
        };
        
        const levelMap = {
            'beginner': '零基础',
            'intermediate': '有一定基础',
            'advanced': '较为熟练'
        };
        
        const message = `
            <h3>🎉 学习计划生成成功！</h3>
            <p><strong>学习目标：</strong>${goalMap[data.goal]}</p>
            <p><strong>当前水平：</strong>${levelMap[data.level]}</p>
            <p><strong>每周学习时间：</strong>${data.studyTime}小时</p>
            <p><strong>预计完成时间：</strong>${data.duration}个月</p>
            <p style="margin-top: 1rem;">我们已为你生成专属学习计划，请查看邮箱获取详细信息。</p>
        `;
        
        showMessage(message, 'success');
        
        // 重置表单
        document.getElementById('planForm').reset();
    }, 2000);
}

function showMessage(message, type) {
    // 创建或获取消息容器
    let messageBox = document.getElementById('messageBox');
    if (!messageBox) {
        messageBox = document.createElement('div');
        messageBox.id = 'messageBox';
        messageBox.style.cssText = `
            position: fixed;
            top: 100px;
            left: 50%;
            transform: translateX(-50%);
            max-width: 500px;
            width: 90%;
            padding: 1.5rem;
            border-radius: 10px;
            box-shadow: 0 5px 20px rgba(0,0,0,0.2);
            z-index: 10000;
            animation: slideDown 0.3s ease-out;
        `;
        document.body.appendChild(messageBox);
    }
    
    // 设置样式
    const colors = {
        'success': { bg: '#d4edda', text: '#155724', border: '#c3e6cb' },
        'error': { bg: '#f8d7da', text: '#721c24', border: '#f5c6cb' },
        'info': { bg: '#d1ecf1', text: '#0c5460', border: '#bee5eb' }
    };
    
    const color = colors[type] || colors['info'];
    messageBox.style.backgroundColor = color.bg;
    messageBox.style.color = color.text;
    messageBox.style.border = `2px solid ${color.border}`;
    messageBox.innerHTML = message;
    
    // 自动隐藏
    setTimeout(() => {
        messageBox.style.animation = 'slideUp 0.3s ease-out';
        setTimeout(() => {
            messageBox.remove();
        }, 300);
    }, 5000);
}

// ======================
// 联系表单
// ======================
function initContactForm() {
    const form = document.getElementById('contactForm');
    const formMessage = document.getElementById('formMessage');
    
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        
        // 获取表单数据
        const formData = {
            name: document.getElementById('name').value.trim(),
            email: document.getElementById('email').value.trim(),
            phone: document.getElementById('phone').value.trim(),
            subject: document.getElementById('subject').value,
            message: document.getElementById('message').value.trim(),
            agree: document.getElementById('agree').checked
        };
        
        // 验证表单
        if (!validateContactForm(formData)) {
            return;
        }
        
        // 提交表单
        submitContactForm(formData);
    });
}

function validateContactForm(data) {
    const formMessage = document.getElementById('formMessage');
    
    // 验证姓名
    if (data.name === '') {
        showFormMessage('请输入你的姓名', 'error');
        return false;
    }
    
    // 验证邮箱
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(data.email)) {
        showFormMessage('请输入有效的邮箱地址', 'error');
        return false;
    }
    
    // 验证电话（如果填写）
    if (data.phone !== '') {
        const phoneRegex = /^1[3-9]\d{9}$/;
        if (!phoneRegex.test(data.phone)) {
            showFormMessage('请输入有效的手机号码', 'error');
            return false;
        }
    }
    
    // 验证主题
    if (data.subject === '') {
        showFormMessage('请选择咨询主题', 'error');
        return false;
    }
    
    // 验证消息
    if (data.message === '' || data.message.length < 10) {
        showFormMessage('请输入至少10个字符的消息内容', 'error');
        return false;
    }
    
    // 验证协议
    if (!data.agree) {
        showFormMessage('请阅读并同意隐私政策和服务条款', 'error');
        return false;
    }
    
    return true;
}

function submitContactForm(data) {
    const submitBtn = document.querySelector('#contactForm button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = '发送中...';
    
    // 模拟API请求
    setTimeout(() => {
        showFormMessage('感谢你的留言！我们会在24小时内回复你。', 'success');
        document.getElementById('contactForm').reset();
        submitBtn.disabled = false;
        submitBtn.textContent = '发送消息';
    }, 1500);
}

function showFormMessage(message, type) {
    const formMessage = document.getElementById('formMessage');
    formMessage.textContent = message;
    formMessage.className = `form-message ${type}`;
    
    // 滚动到消息位置
    formMessage.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    
    // 3秒后自动隐藏
    setTimeout(() => {
        formMessage.className = 'form-message';
    }, 5000);
}

// ======================
// FAQ手风琴
// ======================
function initFAQ() {
    const faqItems = document.querySelectorAll('.faq-item');
    
    faqItems.forEach(item => {
        const question = item.querySelector('.faq-question');
        
        question.addEventListener('click', function() {
            // 关闭其他项
            faqItems.forEach(otherItem => {
                if (otherItem !== item && otherItem.classList.contains('active')) {
                    otherItem.classList.remove('active');
                }
            });
            
            // 切换当前项
            item.classList.toggle('active');
        });
    });
}

// ======================
// 平滑滚动
// ======================
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
        const href = this.getAttribute('href');
        if (href !== '#' && href.length > 1) {
            const target = document.querySelector(href);
            if (target) {
                e.preventDefault();
                target.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start'
                });
            }
        }
    });
});

// ======================
// Toast提示组件
// ======================
function showToast(message, type = 'info', duration = 3000) {
    // 创建toast容器（如果不存在）
    let toastContainer = document.getElementById('toastContainer');
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.id = 'toastContainer';
        toastContainer.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
            display: flex;
            flex-direction: column;
            gap: 10px;
            pointer-events: none;
        `;
        document.body.appendChild(toastContainer);
    }
    
    // 创建toast元素
    const toast = document.createElement('div');
    toast.className = 'toast-message';
    
    // 根据类型设置图标和颜色
    const icons = {
        'success': '✅',
        'error': '❌',
        'warning': '⚠️',
        'info': 'ℹ️'
    };
    
    const colors = {
        'success': { bg: '#d4edda', text: '#155724', border: '#c3e6cb' },
        'error': { bg: '#f8d7da', text: '#721c24', border: '#f5c6cb' },
        'warning': { bg: '#fff3cd', text: '#856404', border: '#ffeaa7' },
        'info': { bg: '#d1ecf1', text: '#0c5460', border: '#bee5eb' }
    };
    
    const color = colors[type] || colors['info'];
    const icon = icons[type] || icons['info'];
    
    toast.innerHTML = `
        <div style="display: flex; align-items: center; gap: 10px;">
            <span style="font-size: 20px;">${icon}</span>
            <span>${message}</span>
        </div>
    `;
    
    toast.style.cssText = `
        background: ${color.bg};
        color: ${color.text};
        border: 2px solid ${color.border};
        padding: 12px 20px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
        font-size: 14px;
        font-weight: 500;
        min-width: 250px;
        max-width: 400px;
        pointer-events: auto;
        animation: slideInRight 0.3s ease-out, fadeOut 0.3s ease-out ${duration - 300}ms;
        opacity: 1;
    `;
    
    toastContainer.appendChild(toast);
    
    // 自动移除
    setTimeout(() => {
        toast.style.animation = 'slideOutRight 0.3s ease-out';
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, duration);
}

// 添加Toast动画样式
if (!document.getElementById('toastStyles')) {
    const style = document.createElement('style');
    style.id = 'toastStyles';
    style.textContent = `
        @keyframes slideInRight {
            from {
                transform: translateX(400px);
                opacity: 0;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        
        @keyframes slideOutRight {
            from {
                transform: translateX(0);
                opacity: 1;
            }
            to {
                transform: translateX(400px);
                opacity: 0;
            }
        }
        
        @keyframes fadeOut {
            from { opacity: 1; }
            to { opacity: 0.8; }
        }
        
        .toast-message:hover {
            transform: scale(1.02);
            transition: transform 0.2s;
        }
    `;
    document.head.appendChild(style);
}

// ======================
// 初始化所有按钮点击事件
// ======================
function initButtonClicks() {
    // 为所有.btn按钮添加点击事件（排除提交按钮）
    const buttons = document.querySelectorAll('.btn:not([type="submit"])');
    
    buttons.forEach(button => {
        // 如果按钮已有特定的点击处理，跳过
        if (button.dataset.hasClickHandler) return;
        
        button.addEventListener('click', function(e) {
            const buttonText = this.textContent.trim();
            
            // 根据按钮文本显示不同的消息
            if (buttonText.includes('立即开始') || buttonText.includes('立即学习')) {
                showToast('🎓 正在进入课程，请稍候...', 'success');
                setTimeout(() => {
                    showToast('课程加载完成！开始你的学习之旅吧 🚀', 'info', 4000);
                }, 1000);
            } else if (buttonText.includes('浏览课程') || buttonText.includes('查看详情')) {
                showToast('📚 正在加载课程详情...', 'info');
            } else if (buttonText.includes('制定计划') || buttonText.includes('选择此路径')) {
                showToast('🎯 正在为你定制学习计划...', 'success', 4000);
                setTimeout(() => {
                    showToast('学习计划已生成！请查看邮箱获取详细信息 📧', 'success', 5000);
                }, 1500);
            } else if (buttonText.includes('继续学习')) {
                showToast('▶️ 恢复学习进度中...', 'info');
                setTimeout(() => {
                    showToast('欢迎回来！继续你的学习 💪', 'success');
                }, 800);
            } else if (buttonText.includes('开始学习')) {
                showToast('🎬 视频加载中...', 'info');
                setTimeout(() => {
                    showToast('准备好了！祝学习愉快 😊', 'success');
                }, 1000);
            } else if (buttonText.includes('加入收藏') || buttonText.includes('收藏')) {
                showToast('⭐ 已添加到收藏夹！', 'success');
            } else {
                showToast(`✨ ${buttonText}功能即将上线，敬请期待！`, 'info');
            }
        });
    });
    
    // 为课程卡片按钮添加特殊处理
    const courseButtons = document.querySelectorAll('.course-card .btn, .course-item .btn');
    courseButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            const courseTitle = this.closest('.course-card, .course-item')?.querySelector('h3')?.textContent;
            if (courseTitle) {
                showToast(`📖 正在进入《${courseTitle}》...`, 'success', 3000);
            }
        });
    });
    
    // 为学习路径按钮添加处理
    const pathButtons = document.querySelectorAll('.path-card .btn');
    pathButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            const pathTitle = this.closest('.path-card')?.querySelector('h3')?.textContent;
            if (pathTitle) {
                showToast(`🎯 你选择了${pathTitle}学习路径`, 'success', 3000);
                setTimeout(() => {
                    showToast('正在生成你的专属学习计划... ⏳', 'info', 3000);
                }, 1000);
            }
        });
    });
    
    // 为资源链接添加处理
    const resourceLinks = document.querySelectorAll('.resource-link, .btn-link');
    resourceLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const resourceName = this.closest('.resource-card, tr')?.querySelector('h3, strong')?.textContent;
            if (resourceName) {
                showToast(`🔗 正在跳转到 ${resourceName}...`, 'info', 2000);
            } else {
                showToast('🌐 正在打开链接...', 'info');
            }
        });
    });
    
    // 为社交链接添加处理
    const socialLinks = document.querySelectorAll('.social-links a');
    socialLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const platform = this.textContent.trim();
            showToast(`📱 正在跳转到${platform}...`, 'info');
        });
    });
}

// ======================
// 返回顶部按钮
// ======================
window.addEventListener('scroll', function() {
    // 可以在这里添加返回顶部按钮的显示/隐藏逻辑
});
