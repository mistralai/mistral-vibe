outputPath = 'build'

// If you want to use the Antora integration, set this to true.
// This requires your project to be setup as Antora module.
// You can use `downloadTemplate` task to bootstrap your project.
//useAntoraIntegration = false

// Path where the docToolchain will search for the input files.
// This path is appended to the docDir property specified in gradle.properties
// or in the command line, and therefore must be relative to it.

inputPath = 'src/docs';

// if you need to register custom Asciidoctor extensions, this is the right place
// configure the name and path to your extension, relative to the root of your project
// (relative to dtcw). For example: 'src/ruby/asciidoctor-lists.rb'.
// this is the same as the `requires`-list of the asciidoctor gradle plugin. The extensions will be
// registered for generateDeck, generateHtml, generatePdf and generateDocbook tasks, only.
// rubyExtensions = []

// the pdfThemeDir config in this file is outdated.
// please check http://doctoolchain.org/docToolchain/v2.0.x/020_tutorial/030_generateHTML.html#_pdf_style for further details
// pdfThemeDir = './src/docs/pdfTheme'

inputFiles = [
        //[file: 'doctoolchain_demo.adoc',       formats: ['html','pdf']],
        //[file: 'arc42-template.adoc',    formats: ['html','pdf']],
	[file: 'arc42/arc42.adoc', formats: ['html','pdf']],
	[file: 'spec/prd-vibe.adoc', formats: ['html','pdf']],
	[file: 'spec/use-cases-vibe.adoc', formats: ['html','pdf']],
	/** inputFiles **/
]

//folders in which asciidoc will find images.
//these will be copied as resources to ./images
//folders are relative to inputPath
// Hint: If you define an imagepath in your documents like
// :imagesdir: ./whatsoever
// define it conditional like
// ifndef::imagesdir[:imagesdir: ./whatsoever]
// as doctoolchain defines :imagesdir: during generation
imageDirs = [
    'images/.',
	'images/.',
	/** imageDirs **/
]

// whether the build should fail when detecting broken image references
// if this config is set to true all images will be embedded
failOnMissingImages = true

// these are directories (dirs) and files which Gradle monitors for a change
// in order to decide if the docs have to be re-build
taskInputsDirs = [
                    "${inputPath}",
//                  "${inputPath}/src",
//                  "${inputPath}/images",
              ]

taskInputsFiles = []

//******************************************************************************

// Configuration for customTasks
// create a new Task with ./dtcw createTask
customTasks = [
/** customTasks **/
]


//******************************************************************************

//Configuration for microsite: generateSite + previewSite

microsite = [:]

// these properties will be set as jBake properties
// microsite.foo will be site.foo in jBake and can be used as config.site_foo in a template
// see https://jbake.org/docs/2.6.4/#configuration for how to configure jBake
// other properties listed here might be used in the jBake templates and thus are not
// documented in the jBake docs but hopefully in the template docs.
microsite.with {
    /** start:microsite **/

    // is your microsite deployed with a context path?
    contextPath = '/'
    // the folder of a site definition (theme) relative to the docDir+inputPath
    //siteFolder = '../site'

    /** end:microsite **/

    //project theme
    //site folder relative to the docs folder
    //see 'copyTheme' for more details
    siteFolder = '../site'

    // the title of the microsite, displayed in the upper left corner
    title = '##site-title##'
    // the next items configure some links in the footer
    //
    // contact eMail
    // example: mailto:bert@example.com
    footerMail = '##footer-email##'
    //
    // twitter account url
    footerTwitter = '##twitter-url##'
    //
    // Stackoverflow QA
    footerSO = '##Stackoverflow-url##'
    //
    // Github Repository
    footerGithub = '##Github-url##'
    //
    // Slack Channel
    footerSlack = '##Slack-url##'
    //
    // Footer Text
    // example: <small class="text-white">built with docToolchain and jBake <br /> theme: docsy</small>
    footerText = '<small class="text-white">built with <a href="https://doctoolchain.org">docToolchain</a> and <a href="https://jbake.org">jBake</a> <br /> theme: <a href="https://www.docsy.dev/">docsy</a></small>'
    //
    // site title if no other title is given
    title = 'docToolchain'
    //
    // the url to create an issue in github
    // Example: https://github.com/docToolchain/docToolchain/issues/new
    issueUrl = '##issue-url##'
    //
    // the base url for code files in github
    // Example: https://github.com/docToolchain/docToolchain/edit/master/src/docs
    branch = System.getenv("DTC_PROJECT_BRANCH")?:'-'
    gitRepoUrl = '##git-repo-url##'

    //
    // the location of the landing page
    landingPage = 'landingpage.gsp'
    // the menu of the microsite. A map of [code:'title'] entries to specify the order and title of the entries.
    // the codes are autogenerated from the folder names or :jbake-menu: attribute entries from the .adoc file headers
    // set a title to '-' in order to remove this menu entry.
    menu = [:]

//tag::additionalConverters[]
/**

if you need support for additional markup converters, you can configure them here
you have three different types of script you can define:

- groovy: just groovy code as string
- groovyFile: path to a groovy script
- bash: a bash command. It will receive the name of the file to be converted as first argument

`groovy` and `groovyFile` will have access to the file and config object

`dtcw:rstToHtml.py` is an internal script to convert restructuredText.
Needs `python3` and `docutils` installed.

**/
    additionalConverters = [
        //'.one': [command: 'println "test"+file.canonicalPath', type: 'groovy'],
        //'.two': [command: 'scripts/convert-md.groovy', type: 'groovyFile'],
        //'.rst': [command: 'dtcw:rstToHtml.py', type: 'bash'],
    ]
//end::additionalConverters[]

    // if you prefer another convention regarding the automatic generation
    // of jBake headers, you can configure a script to modify them here
    // the script has access to
    // - file: the current object
    // - sourceFolder: the copy of the docs-source on which the build operates
    //                 default `/microsite/tmp/site/doc`
    // - config: the config object (this file, but parsed)
    // - headers: already parsed headers to be modified
    /**
    customConvention = """
        System.out.println file.canonicalPath
        headers.title += " - from CustomConvention"
    """.stripIndent()
    **/

    // define a custom search html
    /**
    search = """        <form action="${content.rootpath}search.html">
        <input aria-label="Search this site…" autocomplete="off" class="form-control td-search-input"
               placeholder="🔍 Search this site…" type="search" name="q">
        </form>
    """
    **/
}

//******************************************************************************

//Configuration for exportChangelog

exportChangelog = [:]

changelog.with {

    // Directory of which the exportChangelog task will export the changelog.
    // It should be relative to the docDir directory provided in the
    // gradle.properties file.
    dir = 'src/docs'

    // Command used to fetch the list of changes.
    // It should be a single command taking a directory as a parameter.
    // You cannot use multiple commands with pipe between.
    // This command will be executed in the directory specified by changelogDir
    // it the environment inherited from the parent process.
    // This command should produce asciidoc text directly. The exportChangelog
    // task does not do any post-processing
    // of the output of that command.
    //
    // See also https://git-scm.com/docs/pretty-formats
    cmd = 'git log --pretty=format:%x7c%x20%ad%x20%n%x7c%x20%an%x20%n%x7c%x20%s%x20%n --date=short'

}

//******************************************************************************

//tag::confluenceConfig[]
//Configuration for publishToConfluence

confluence = [:]

/**
//tag::input-config[]

*input*

is an array of files to upload to Confluence with the ability
to configure a different parent page for each file.

=== Attributes

- `file`: absolute or relative path to the asciidoc generated html file to be exported
- `url`: absolute URL to an asciidoc generated html file to be exported
- `ancestorName` (optional): the name of the parent page in Confluence as string;
                              this attribute has priority over ancestorId, but if page with given name doesn't exist,
                              ancestorId will be used as a fallback
- `ancestorId` (optional): the id of the parent page in Confluence as string; leave this empty
                           if a new parent shall be created in the space

The following four keys can also be used in the global section below

- `spaceKey`: page specific variable for the key of the confluence space to write to
              case sensitive! If the case is not correct, it can be that new page will be
              created but can't be updated in the next run.
- `subpagesForSections` (optional): The number of nested sub-pages to create. Default is '1'.
                                    '0' means creating all on one page.
                             The following migration for removed configuration can be used.
** `allInOnePage = true` is the same as `subpagesForSections = 0`
** `allInOnePage = false && createSubpages = false` is the same as `subpagesForSections = 1`
** `allInOnePage = false && createSubpages = true` is the same as `subpagesForSections = 2`
- `pagePrefix` (optional): page specific variable, the pagePrefix will be a prefix for the page title and it's sub-pages
                          use this if you only have access to one confluence space but need to store several
                          pages with the same title - a different pagePrefix will make them unique
- `pageSuffix` (optional): same usage as prefix but appended to the title and it's subpages

only 'file' or 'url' is allowed. If both are given, 'url' is ignored

//end::input-config[]
**/

confluence.with {
    input = [
            [ file: "build/html5/arc42-template-de.html" ],
    ]

    // endpoint of the confluenceAPI (REST) to be used
    //
    // For Confluence Cloud, set this to:
    // https://[yourDomain].atlassian.net
    // Example: https://arc42-template.atlassian.net
    //
    // For Confluence Server/Data Center, you may need to include a context:
    // https://[yourServer]/[context]
    // Example: https://confluence.company.com/wiki
    //
    // VERIFICATION: You can verify your endpoint is correct by browsing to:
    // [your-api-endpoint]/rest/api/user/current
    // This should return a JSON response describing your current user
    // Working example: https://arc42-template.atlassian.net/wiki/rest/api/user/current
    api = 'https://[yourServer]/[context]'

    // requests per second for confluence API calls
    rateLimit = 10

    // if true API V1 only will be used. Default is true.
    // useV1Api = true

    // if true, the new editor v2 will be used. Default is false.
    // enforceNewEditor = false

    //     Additionally, spaceKey, subpagesForSections, pagePrefix and pageSuffix can be globally defined here. The assignment in the input array has precedence

    // the key of the confluence space to write to
    spaceKey = 'asciidoc'

    // if true, all pages will be created using the new editor v2
    // enforceNewEditor = false

    // variable to determine how many layers of sub pages should be created
    subpagesForSections = 1

    // the pagePrefix will be a prefix for each page title
    // use this if you only have access to one confluence space but need to store several
    // pages with the same title - a different pagePrefix will make them unique
    pagePrefix = ''

    pageSuffix = ''

    // the comment used for the page version
    pageVersionComment = ''

    /*
    WARNING: It is strongly recommended to store credentials securely instead of committing plain text values to your git repository!!!

    Tool expects credentials that belong to an account which has the right permissions to to create and edit confluence pages in the given space.
    Credentials can be used in a form of:
     - passed parameters when calling script (-PconfluenceUser=myUsername -PconfluencePass=myPassword) which can be fetched as a secrets on CI/CD or
     - gradle variables set through gradle properties (uses the 'confluenceUser' and 'confluencePass' keys)
    Often, same credentials are used for Jira & Confluence, in which case it is recommended to pass CLI parameters for both entities as
    -Pusername=myUser -Ppassword=myPassword
    */

    //optional API-token to be added in case the credentials are needed for user and password exchange.
    //apikey = "[API-token]"

    // HTML Content that will be included with every page published
    // directly after the TOC. If left empty no additional content will be
    // added
    // extraPageContent = '<ac:structured-macro ac:name="warning"><ac:parameter ac:name="title" />><ac:rich-text-body>This is a generated page, do not edit!</ac:rich-text-body></ac:structured-macro>
    extraPageContent = ''

    // enable or disable attachment uploads for local file references
    enableAttachments = false

    // variable to limit number of pages retreived per REST-API call
    pageLimit = 100

    // default attachmentPrefix = attachment - All files to attach will require to be linked inside the document.
    // attachmentPrefix = "attachment"


    // Optional proxy configuration, only used to access Confluence
    // schema supports http and https
    // proxy = [host: 'my.proxy.com', port: 1234, schema: 'http']

    // Optional: specify which Confluence OpenAPI Macro should be used to render OpenAPI definitions
    // possible values: ["confluence-open-api", "open-api", true]. true is the same as "confluence-open-api" for backward compatibility
    // useOpenApiMacro = "confluence-open-api"

    // for exportConfluence-Task
    export = [
        srcDir: 'sample_data',
        destDir: 'src/docs'
    ]

}
//end::confluenceConfig[]

//******************************************************************************
//tag::exportEAConfig[]
//Configuration for the export script 'exportEA.vbs'.
// The following parameters can be used to change the default behaviour of 'exportEA'.
// All parameter are optionally.
// Parameter 'connection' allows to select a certain database connection by using the ConnectionString as used for
// directly connecting to the project database instead of looking for EAP/EAPX files inside and below the 'src' folder.
// Parameter 'packageFilter' is an array of package GUID's to be used for export. All images inside and in all packages below the package represented by its GUID are exported.
// A packageGUID, that is not found in the currently opened project, is silently skipped.
// PackageGUID of multiple project files can be mixed in case multiple projects have to be opened.

exportEA.with {
// OPTIONAL: Set the connection to a certain project or comment it out to use all project files inside the src folder or its child folder.
// connection = "DBType=1;Connect=Provider=SQLOLEDB.1;Integrated Security=SSPI;Persist Security Info=False;Initial Catalog=[THE_DB_NAME_OF_THE_PROJECT];Data Source=[server_hosting_database.com];LazyLoad=1;"
// OPTIONAL: Add one or multiple packageGUIDs to be used for export. All packages are analysed, if no packageFilter is set.
// packageFilter = [
//                  "{A237ECDE-5419-4d47-AECC-B836999E7AE0}",
//                  "{B73FA2FB-267D-4bcd-3D37-5014AD8806D6}"
//                 ]
// OPTIONAL: tag used to filter diagrams to be exported - based on diagram.Stereotype.
//           If not set all diagrams in the selected packages are exported.
// diagramFilter = "myProject"
// OPTIONAL: relative path to base 'docDir' to which the diagrams and notes are to be exported
// exportPath = "src/docs/"
// OPTIONAL: relative path to base 'docDir', in which Enterprise Architect project files are searched
// searchPath = "src/docs/"

}
//end::exportEAConfig[]

//tag::htmlSanityCheckConfig[]
htmlSanityCheck.with {
    //sourceDir = "build/html5/site"
    //checkingResultsDir =
}
//end::htmlSanityCheckConfig[]

//tag::jiraConfig[]
// Configuration for Jira related tasks
jira = [:]

jira.with {

    // endpoint of the JiraAPI (REST) to be used
    api = 'https://your-jira-instance'

    // requests per second for jira API calls
    rateLimit = 10

    /*
    WARNING: It is strongly recommended to store credentials securely instead of committing plain text values to your git repository!!!

    Tool expects credentials that belong to an account which has the right permissions to read the JIRA issues for a given project.
    Credentials can be used in a form of:
     - passed parameters when calling script (-PjiraUser=myUsername -PjiraPass=myPassword) which can be fetched as a secrets on CI/CD or
     - gradle variables set through gradle properties (uses the 'jiraUser' and 'jiraPass' keys)
    Often, Jira & Confluence credentials are the same, in which case it is recommended to pass CLI parameters for both entities as
    -Pusername=myUser -Ppassword=myPassword
    */

    // the key of the Jira project
    project = 'PROJECTKEY'

    // the format of the received date time values to parse
    dateTimeFormatParse = "yyyy-MM-dd'T'H:m:s.SSSZ" // i.e. 2020-07-24'T'9:12:40.999 CEST

    // the format in which the date time should be saved to output
    dateTimeFormatOutput = "dd.MM.yyyy HH:mm:ss z" // i.e. 24.07.2020 09:02:40 CEST

    // the label to restrict search to
    label =

    // Legacy settings for Jira query. This setting is deprecated & support for it will soon be completely removed. Please use JiraRequests settings
    //jql = "project='%jiraProject%' AND labels='%jiraLabel%' ORDER BY priority DESC, duedate ASC"

    // Base filename in which Jira query results should be stored
    resultsFilename = 'JiraTicketsContent'

    saveAsciidoc = true // if true, asciidoc file will be created with *.adoc extension
    saveExcel = true // if true, Excel file will be created with *.xlsx extension

    // Output folder for this task inside main outputPath
    resultsFolder = 'JiraRequests'

    /*
    List of requests to Jira API:
    These are basically JQL expressions bundled with a filename in which results will be saved.
    User can configure custom fields IDs and name those for column header,
    i.e. customfield_10026:'Story Points' for Jira instance that has custom field with that name and will be saved in a column named "Story Points"
    */
    exports = [
        [
            filename:"File1_Done_issues",
            jql:"project='%jiraProject%' AND status='Done' ORDER BY duedate ASC",
            customfields: [customfield_10026:'Story Points']
        ],
        [
            filename:'CurrentSprint',
            jql:"project='%jiraProject%' AND Sprint in openSprints() ORDER BY priority DESC, duedate ASC",
            customfields: [customfield_10026:'Story Points']
        ],
    ]
}
//end::jiraConfig[]

//tag::openApiConfig[]
// Configuration for OpenAPI related task
openApi = [:]

// 'specFile' is the name of OpenAPI specification yaml file. Tool expects this file inside working dir (as a filename or relative path with filename)
// 'infoUrl' and 'infoEmail' are specification metadata about further info related to the API. By default this values would be filled by openapi-generator plugin placeholders
//

openApi.with {
    specFile = 'src/docs/petstore-v2.0.yaml' // i.e. 'petstore.yaml', 'src/doc/petstore.yaml'
    infoUrl = 'https://my-api.company.com'
    infoEmail = 'info@company.com'
}
//end::openApiConfig[]

//tag::sprintChangelogConfig[]
// Sprint changelog configuration generate changelog lists based on tickets in sprints of an Jira instance.
// This feature requires at least Jira API & credentials to be properly set in Jira section of this configuration
sprintChangelog = [:]
sprintChangelog.with {
    sprintState = 'closed' // it is possible to define multiple states, i.e. 'closed, active, future'
    ticketStatus = "Done, Closed" // it is possible to define multiple ticket statuses, i.e. "Done, Closed, 'in Progress'"

    showAssignee = false
    showTicketStatus = false
    showTicketType = true
    sprintBoardId = 12345  // Jira instance probably have multiple boards; here it can be defined which board should be used

    // Output folder for this task inside main outputPath
    resultsFolder = 'Sprints'

    // if sprintName is not defined or sprint with that name isn't found, release notes will be created on for all sprints that match sprint state configuration
    sprintName = 'PRJ Sprint 1' // if sprint with a given sprintName is found, release notes will be created just for that sprint
    allSprintsFilename = 'Sprints_Changelogs' // Extension will be automatically added.
}
//end::sprintChangelogConfig[]

//tag::collectIncludesConfig[]
collectIncludes = [:]

collectIncludes.with {

    fileFilter = "adoc" // define which files are considered. default: "ad|adoc|asciidoc"

    minPrefixLength = "3" // define what minimum length the prefix. default: "3"

    maxPrefixLength = "3" // define what maximum length the prefix. default: ""

    separatorChar = "_" // define the allowed separators after prefix. default: "-_"

    cleanOutputFolder = true // should the output folder be emptied before generation? default: false

    excludeDirectories = [] // define additional directories that should not be traversed.

}
//end::collectIncludesConfig[]

//tag::structurizrConfig[]
// Configuration for Structurizr related tasks
structurizr = [:]

structurizr.with {

    // Configure where `exportStructurizr` looks for the Structurizr model.
    workspace = {
        // The directory in which the Structurizr workspace file is located.
        // path = 'src/docs/structurizr'

        // By default `exportStructurizr` looks for a file '${structurizr.workspace.path}/workspace.dsl'.
        // You can customize this behaviour with 'filename'. Note that the workspace filename is provided without '.dsl' extension.
        // filename = 'workspace'
    }

    export = {
        // Directory for the exported diagrams.
        //
        // WARNING: Do not put manually created/changed files into this directory.
        // If a valid Structurizr workspace file is found the directory is deleted before the diagram files are generated.
        // outputPath = 'src/docs/structurizr/diagrams'

        // Format of the exported diagrams. Defaults to 'plantuml' if the parameter is not provided.
        //
        // Following formats are supported:
        // - 'plantuml': the same as 'plantuml/structurizr'
        // - 'plantuml/structurizr': exports views to PlantUML
        // - 'plantuml/c4plantuml': exports views to PlantUML with https://github.com/plantuml-stdlib/C4-PlantUML
        // format = 'plantuml'
    }
}
//end::structurizrConfig[]

//tag::openAIConfig[]
// Configuration for openAI related tasks
openAI = [:]

openAI.with {
    // This task requires a person access token for openAI.
    // Ensure to pass this token as parameters when calling the task
    // using -PopenAI.token=xx-xxxxxxxxxxxx

    //model = "text-davinci-003"
    //maxToken = '500'
    //temperature = '0.3'
}
//end::openAIConfig[]
