
import fs  from "node:fs";
import net from "node:net";
import path from "node:path";

import { fileURLToPath as url_fileURLToPath } from "node:url";

const SocketPath = "/tmp/sublime.buildsock.sock";


function getBasePath()
{
    let base = new URL(".", import.meta.url);
    return url_fileURLToPath(base);
}


class Project {

    constructor(name, dir)
    {
        let watcher = fs.watch(dir, { persistent: true, recursive: true });

        watcher.on("change", (event, filename) => {
            if (filename.match(/(.*?)\.tmp$/)) {
                return;
            }

            filename = dir + "/" + filename;

            this.startBuild();
        });

        this._name = name;
        this._path = dir;
        this._watcher = watcher;
        this._issues = [ ];
        this._building = false;
        this._needsBuild = false;
        this._timeout = 0;
    }
    
    async sendCommands(commands)
    {
        return new Promise((resolve, reject) => {
            let contents = {
                "project": this._path,
                "commands": commands 
            };
            
            console.log(`Project '${this._name}' sending:`);
            console.log(JSON.stringify(contents, null, "    "));
            console.log("");

            let socket = net.connect(SocketPath, () => {
                socket.write(JSON.stringify(contents), () => {
                    socket.end(() => {
                        resolve();
                    });
                })
            });

            socket.on("error", e => {
                console.log(`Project '${this._name}', socket error:`);
                console.error(e)
                console.log("")
                
                reject(e);
            });
        })
    }

    async clear()
    {
        if (this._timeout) {
            clearTimeout(this._timeout);
            this._timeout = 0;
        }

        return this.sendCommands([ { "command": "clear" } ]);
    }

    startBuild()
    {
        console.log(`Project '${this._name}' starting build.`);

        if (this._building) {
            this._needsBuild = true;
            return;
        }

        this._building = true;

        this.sendCommands([
            {
                "command": "show-status",
                "message": "Building",
                "spinner": "clock"
            }
        ]);
        
        // Delay for a bit to mimic a real build process
        let delayInMs = 250 + (Math.random() * 750);

        if (this._timeout) clearTimeout(this._timeout);
        this._timeout = setTimeout(() => { this._finishBuild(delayInMs); }, delayInMs);

        let issues = [ ];

        for (let file of fs.globSync("**/*.txt", { "cwd": this._path })) {
            let contents = fs.readFileSync(path.join(this._path, file));
            let lines = contents.toString().split("\n");
            let line = 1;
            let issue = null;
            let details = null;
            
            for (let i = 0; i < lines.length; i++) {
                let line = lines[i];

                if (details) {
                    if (line.trim() == "") {
                        if (issue) {
                            issue["details"] = details.join("\n");
                        }

                        details = null;
                        issue = null;

                        continue;
                    } else {
                        details.push(line);
                    }
                }

                let m = line.match(/^(GENERIC|INFO|WARN|WARNING|ERROR):\s*(.*?)$/);
                if (!m) continue;

                issue = {
                    "file": file,
                    "line": i + 1,
                    "message": m[2].trim()
                };

                details = [ ];
                
                issues.push(issue);

                let type = {
                    "INFO":    "info",
                    "WARN":    "warning",
                    "WARNING": "warning",
                    "ERROR":   "error"
                }[m[1]];
                
                if (type) issue["type"] = type;
            }
        }
        
        this._issues = issues;
    }

    _finishBuild(buildTimeInMs)
    {
        console.log(`Project '${this._name}' finished build.`);

        let issues = this._issues;
        
        if (issues.length) {
            this.sendCommands([
                {
                    "command": "show-issues",
                    "issues": issues
                }, {
                    "command": "show-status",
                    "message": "Build failed"
                }
            ]);
        
        } else {
            let buildTime = Math.round(buildTimeInMs / 100) / 10;

            this.sendCommands([
                {
                    "command": "hide-issues"
                }, {
                    "command": "show-status",
                    "message": `Build finished in ${buildTime} seconds`
                }
            ])        
        }

        this._building = false;

        if (this._needsBuild) {
            this._needsBuild = false;
            
            if (this._timeout) clearTimeout(this._timeout);
            this._timeout = setTimeout(() => { this.startBuild(); }, 0);
        }
    }
}


function main()
{
    let projects = [ ];

    let basePath = getBasePath();
    for (let projectPath of fs.globSync("project*/", { cwd: basePath })) {
        let project = new Project(projectPath, path.join(basePath, projectPath));
        project.startBuild();
        projects.push(project);
    }

    process.on("SIGINT", async () => {
        for (let project of projects) {
            await project.clear();
        }
        
        process.exit();
    })
}


main();

